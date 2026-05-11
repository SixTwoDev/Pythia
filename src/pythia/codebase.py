import asyncio
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CLONE_TIMEOUT_SECONDS = 300
REFRESH_TIMEOUT_SECONDS = 120
KILL_REAP_TIMEOUT_SECONDS = 5
DEFAULT_REFRESH_INTERVAL_SECONDS = 3600
SEARCH_MAX_RESULTS = 50
SEARCH_MAX_PER_FILE = 10
READ_DEFAULT_LINES = 400

# Files we look for at the root of each cloned repo to ground the agent in
# the project's conventions. First match wins per repo. Order is intentional:
# CLAUDE.md is the most opinionated and detailed when present; AGENTS.md is
# the cross-tool fallback.
GROUNDING_DOC_CANDIDATES = ("CLAUDE.md", "AGENTS.md")
GROUNDING_DOC_MAX_CHARS = 6000


@dataclass(frozen=True)
class RepoSpec:
    name: str
    url: str


@dataclass(frozen=True)
class Repo:
    name: str
    url: str
    local_path: Path


def parse_repos(spec: str | None) -> list[RepoSpec]:
    if not spec:
        return []
    return [_parse_one(token) for token in spec.split(",") if token.strip()]


def _parse_one(token: str) -> RepoSpec:
    token = token.strip()
    eq_idx = token.find("=")
    proto_idx = token.find("://")
    if eq_idx >= 0 and (proto_idx == -1 or eq_idx < proto_idx):
        name, url = token[:eq_idx].strip(), token[eq_idx + 1 :].strip()
    else:
        url = token
        name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git").rsplit(":", 1)[-1]
    if not name or not url:
        raise ValueError(f"invalid CODEBASE_REPOS entry: {token!r}")
    return RepoSpec(name=name, url=url)


def require_binaries(*names: str) -> None:
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        raise RuntimeError(
            f"required binaries not found on PATH: {', '.join(missing)}. "
            "Install ripgrep and git, or unset CODEBASE_REPOS."
        )


async def _kill_and_reap(proc: asyncio.subprocess.Process, name: str) -> None:
    """SIGKILL a runaway subprocess and wait for it to exit, so it doesn't
    linger as a zombie and the stdout/stderr pipes get closed. Bounded by
    KILL_REAP_TIMEOUT_SECONDS in case the kernel itself is wedged."""
    proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=KILL_REAP_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("subprocess %s did not exit after SIGKILL", name)


async def clone_repo(spec: RepoSpec, base_dir: Path) -> Repo:
    target = base_dir / spec.name
    proc = await asyncio.create_subprocess_exec(
        "git",
        "clone",
        "--depth",
        "1",
        "--",
        spec.url,
        str(target),
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLONE_TIMEOUT_SECONDS)
    except TimeoutError:
        await _kill_and_reap(proc, f"git clone {spec.name}")
        raise RuntimeError(f"git clone timed out for {spec.name}") from None
    if proc.returncode != 0:
        raise RuntimeError(
            f"git clone failed for {spec.name}: {stderr.decode(errors='replace').strip()}"
        )
    logger.info("cloned %s into %s", spec.name, target)
    return Repo(name=spec.name, url=spec.url, local_path=target)


async def clone_all(specs: list[RepoSpec], base_dir: Path) -> dict[str, Repo]:
    if not specs:
        return {}
    results = await asyncio.gather(*(clone_repo(s, base_dir) for s in specs))
    return {r.name: r for r in results}


async def refresh_repo(repo: Repo) -> bool:
    """Force the local clone back into lockstep with its remote default branch.

    Pythia never makes local commits, so a hard reset to FETCH_HEAD is both
    safe and the only behaviour that's guaranteed to converge — `pull` would
    fail on a remote force-push, on a default-branch rename, or on any
    history rewrite. Returns True on success, False on any git failure.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    git = ("git", "-C", str(repo.local_path))
    for argv in (
        (*git, "fetch", "--depth", "1", "origin"),
        (*git, "reset", "--hard", "FETCH_HEAD"),
    ):
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=REFRESH_TIMEOUT_SECONDS)
        except TimeoutError:
            await _kill_and_reap(proc, f"git {argv[3]} {repo.name}")
            logger.warning("refresh of %s timed out on %s", repo.name, argv[3])
            return False
        if proc.returncode != 0:
            logger.warning(
                "refresh of %s failed on `git %s`: %s",
                repo.name,
                argv[3],
                stderr.decode(errors="replace").strip(),
            )
            return False
    logger.info("refreshed %s", repo.name)
    return True


async def run_refresh_loop(
    repos: dict[str, Repo],
    locks: dict[str, asyncio.Lock],
    interval_seconds: int,
) -> None:
    """Background task: every `interval_seconds`, fetch + reset every repo
    against its remote. Holds the per-repo lock during refresh so `read_file`
    and `search_code` never see mid-update state.

    Disabled when interval_seconds <= 0; the loop simply returns.
    """
    if interval_seconds <= 0 or not repos:
        return
    logger.info(
        "starting codebase refresh loop: %d repo(s) every %ds",
        len(repos),
        interval_seconds,
    )
    while True:
        await asyncio.sleep(interval_seconds)
        for name, repo in repos.items():
            async with locks[name]:
                try:
                    await refresh_repo(repo)
                except Exception:
                    logger.exception("unexpected error refreshing %s", name)


def read_grounding_docs(repos: dict[str, Repo]) -> str:
    """Read the first matching CLAUDE.md / AGENTS.md from each repo and join
    them into a single grounding section the agent can read at startup.

    Each file is capped at ~6k chars so a few large guides don't dominate the
    context window. Empty string when no repo has any of these files.

    Symlinks are rejected: a malicious repo could ship `CLAUDE.md` as a
    symlink to `/etc/passwd` or `~/.ssh/id_rsa` and exfiltrate it into the
    system prompt. We resolve the candidate path and require it to live
    inside the cloned repo root.
    """
    sections: list[str] = []
    for name, repo in repos.items():
        for filename in GROUNDING_DOC_CANDIDATES:
            content = _read_grounding_doc(name, repo, filename)
            if content is None:
                continue
            sections.append(f"## {name} ({filename})\n\n{content}")
            break
    return "\n\n---\n\n".join(sections)


def _read_grounding_doc(name: str, repo: Repo, filename: str) -> str | None:
    candidate = repo.local_path / filename
    if not candidate.exists():
        return None
    if candidate.is_symlink():
        logger.warning("ignoring symlinked grounding doc %s/%s", name, filename)
        return None
    root = repo.local_path.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        logger.warning(
            "ignoring grounding doc %s/%s — escapes repo root or not a file", name, filename
        )
        return None
    content = resolved.read_text(encoding="utf-8", errors="replace").strip()
    if len(content) > GROUNDING_DOC_MAX_CHARS:
        content = content[:GROUNDING_DOC_MAX_CHARS] + "\n\n[…truncated]"
    logger.info("loaded grounding doc %s/%s (%d chars)", name, filename, len(content))
    return content


def build_codebase_tools(
    repos: dict[str, Repo],
    locks: dict[str, asyncio.Lock] | None = None,
) -> list[Callable[..., object]]:
    if not repos:
        return []
    repo_locks = locks if locks is not None else {name: asyncio.Lock() for name in repos}

    async def search_code(repo: str, query: str) -> str:
        """Search a repo for a regex pattern using ripgrep.

        Args:
            repo: Name of one of the configured codebase repos.
            query: ripgrep regex pattern (PCRE2 syntax).

        Returns:
            Up to 50 matching `path:line:text` entries (max 10 per file), or a
            short message if there are no matches or the repo is unknown.
        """
        if repo not in repos:
            return f"unknown repo {repo!r}; configured: {sorted(repos)}"
        root = repos[repo].local_path
        async with repo_locks[repo]:
            proc = await asyncio.create_subprocess_exec(
                "rg",
                "--no-heading",
                "--line-number",
                "--max-count",
                str(SEARCH_MAX_PER_FILE),
                "--",
                query,
                str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        if proc.returncode == 1:
            return f"no matches for {query!r} in {repo}"
        if proc.returncode != 0:
            return f"search failed: {stderr.decode(errors='replace').strip()}"
        prefix = str(root) + "/"
        lines = [line.removeprefix(prefix) for line in stdout.decode(errors="replace").splitlines()]
        return "\n".join(lines[:SEARCH_MAX_RESULTS])

    async def read_file(
        repo: str, path: str, start_line: int = 1, end_line: int = READ_DEFAULT_LINES
    ) -> str:
        """Read a file from a repo, optionally restricted to a line range.

        Args:
            repo: Name of one of the configured codebase repos.
            path: Path within the repo, e.g. "src/api/users.py".
            start_line: First line to include (1-indexed).
            end_line: Last line to include (inclusive, 1-indexed).

        Returns:
            File contents prefixed with line numbers, or a short message if the
            repo is unknown, the path escapes the repo, or the file is missing.
        """
        if repo not in repos:
            return f"unknown repo {repo!r}; configured: {sorted(repos)}"
        root = repos[repo].local_path.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            return f"path {path!r} escapes the repo root"
        async with repo_locks[repo]:
            if not target.is_file():
                return f"not a file: {path}"
            text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, start_line)
        end = min(len(lines), end_line)
        if start > end:
            return ""
        width = len(str(end))
        return "\n".join(f"{i:>{width}}: {lines[i - 1]}" for i in range(start, end + 1))

    return [search_code, read_file]
