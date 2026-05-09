import asyncio
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CLONE_TIMEOUT_SECONDS = 300
SEARCH_MAX_RESULTS = 50
SEARCH_MAX_PER_FILE = 10
READ_DEFAULT_LINES = 400


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
        env={"GIT_TERMINAL_PROMPT": "0"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLONE_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
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


def build_codebase_tools(repos: dict[str, Repo]) -> list[Callable[..., object]]:
    if not repos:
        return []

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
