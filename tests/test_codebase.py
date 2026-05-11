import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest

from pythia import codebase as codebase_module
from pythia.codebase import (
    GROUNDING_DOC_MAX_CHARS,
    Repo,
    RepoSpec,
    build_codebase_tools,
    clone_repo,
    parse_repos,
    read_grounding_docs,
    refresh_repo,
    require_binaries,
    run_refresh_loop,
)

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")


def test_parse_repos_returns_empty_list_for_none() -> None:
    assert parse_repos(None) == []


def test_parse_repos_returns_empty_list_for_empty_string() -> None:
    assert parse_repos("") == []


def test_parse_repos_with_explicit_name_equals_url() -> None:
    assert parse_repos("api=git@github.com:acme/api.git") == [
        RepoSpec(name="api", url="git@github.com:acme/api.git"),
    ]


def test_parse_repos_derives_name_from_ssh_url() -> None:
    assert parse_repos("git@github.com:acme/api.git") == [
        RepoSpec(name="api", url="git@github.com:acme/api.git"),
    ]


def test_parse_repos_derives_name_from_https_url() -> None:
    assert parse_repos("https://github.com/acme/web.git") == [
        RepoSpec(name="web", url="https://github.com/acme/web.git"),
    ]


def test_parse_repos_handles_explicit_name_with_https_url() -> None:
    assert parse_repos("web=https://github.com/acme/web.git") == [
        RepoSpec(name="web", url="https://github.com/acme/web.git"),
    ]


def test_parse_repos_splits_multiple_entries_and_trims_whitespace() -> None:
    assert parse_repos("api=git@github.com:acme/api.git, web=https://example.com/web.git") == [
        RepoSpec(name="api", url="git@github.com:acme/api.git"),
        RepoSpec(name="web", url="https://example.com/web.git"),
    ]


def test_parse_repos_raises_on_empty_token_around_equals() -> None:
    with pytest.raises(ValueError, match="invalid CODEBASE_REPOS entry"):
        parse_repos("=")


def test_require_binaries_raises_when_missing() -> None:
    with pytest.raises(RuntimeError, match="not found on PATH"):
        require_binaries("definitely-not-a-real-binary-9999")


@pytest.mark.asyncio
async def test_clone_repo_inherits_parent_environment_and_disables_terminal_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"")

    async def _fake_exec(*args: object, **kwargs: object) -> _FakeProc:
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(codebase_module.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setenv("PYTHIA_TEST_MARKER", "inherited")

    await clone_repo(RepoSpec(name="x", url="git@example.com:x.git"), tmp_path)

    env = captured.get("env")
    assert isinstance(env, dict)
    assert env["PYTHIA_TEST_MARKER"] == "inherited"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "PATH" in env, "PATH must be inherited so git can find ssh and helpers"


@pytest.fixture
def fake_repo(tmp_path: Path) -> Repo:
    root = tmp_path / "myrepo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text(
        "def hello() -> None:\n    print('world')\n\nVERSION = '1.0'\n",
        encoding="utf-8",
    )
    (root / "src" / "util.py").write_text("ANSWER = 42\n", encoding="utf-8")
    (root / "README.md").write_text("# Fake repo\n", encoding="utf-8")
    return Repo(name="myrepo", url="fake://", local_path=root)


def _tool(name: str, fake_repo: Repo) -> Callable[..., Awaitable[str]]:
    tools = build_codebase_tools({fake_repo.name: fake_repo})
    return cast(Callable[..., Awaitable[str]], next(t for t in tools if t.__name__ == name))


def test_build_codebase_tools_returns_no_tools_for_empty_repo_set() -> None:
    assert build_codebase_tools({}) == []


def test_build_codebase_tools_returns_search_and_read() -> None:
    fake = Repo(name="r", url="x", local_path=Path("/tmp"))
    tools = build_codebase_tools({"r": fake})
    assert {t.__name__ for t in tools} == {"search_code", "read_file"}


@needs_rg
@pytest.mark.asyncio
async def test_search_code_finds_a_match(fake_repo: Repo) -> None:
    search = _tool("search_code", fake_repo)
    out = await search("myrepo", "VERSION")
    assert "src/main.py" in out
    assert "VERSION" in out


@needs_rg
@pytest.mark.asyncio
async def test_search_code_returns_no_match_message(fake_repo: Repo) -> None:
    search = _tool("search_code", fake_repo)
    out = await search("myrepo", "definitely_not_present_anywhere")
    assert out == "no matches for 'definitely_not_present_anywhere' in myrepo"


@pytest.mark.asyncio
async def test_search_code_rejects_unknown_repo(fake_repo: Repo) -> None:
    search = _tool("search_code", fake_repo)
    out = await search("nope", "x")
    assert "unknown repo 'nope'" in out


@pytest.mark.asyncio
async def test_read_file_returns_file_contents_with_line_numbers(fake_repo: Repo) -> None:
    read = _tool("read_file", fake_repo)
    out = await read("myrepo", "src/util.py")
    assert out == "1: ANSWER = 42"


@pytest.mark.asyncio
async def test_read_file_respects_line_range(fake_repo: Repo) -> None:
    read = _tool("read_file", fake_repo)
    out = await read("myrepo", "src/main.py", 1, 2)
    assert out == "1: def hello() -> None:\n2:     print('world')"


@pytest.mark.asyncio
async def test_read_file_blocks_path_traversal(fake_repo: Repo, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    read = _tool("read_file", fake_repo)
    out = await read("myrepo", "../secret.txt")
    assert "escapes the repo root" in out


@pytest.mark.asyncio
async def test_read_file_reports_missing_file(fake_repo: Repo) -> None:
    read = _tool("read_file", fake_repo)
    out = await read("myrepo", "src/does_not_exist.py")
    assert out == "not a file: src/does_not_exist.py"


@pytest.mark.asyncio
async def test_read_file_rejects_unknown_repo(fake_repo: Repo) -> None:
    read = _tool("read_file", fake_repo)
    out = await read("nope", "any.py")
    assert "unknown repo 'nope'" in out


def _repo_with_files(tmp_path: Path, name: str, files: dict[str, str]) -> Repo:
    root = tmp_path / name
    root.mkdir()
    for filename, content in files.items():
        (root / filename).write_text(content, encoding="utf-8")
    return Repo(name=name, url="fake://", local_path=root)


def test_read_grounding_docs_returns_empty_when_no_repos_configured() -> None:
    assert read_grounding_docs({}) == ""


def test_read_grounding_docs_returns_empty_when_no_repo_has_grounding_files(
    tmp_path: Path,
) -> None:
    repos = {"r": _repo_with_files(tmp_path, "r", {"README.md": "just a readme"})}
    assert read_grounding_docs(repos) == ""


def test_read_grounding_docs_picks_up_claude_md(tmp_path: Path) -> None:
    repos = {"r": _repo_with_files(tmp_path, "r", {"CLAUDE.md": "use tabs not spaces"})}
    out = read_grounding_docs(repos)
    assert "## r (CLAUDE.md)" in out
    assert "use tabs not spaces" in out


def test_read_grounding_docs_falls_back_to_agents_md_when_claude_md_absent(
    tmp_path: Path,
) -> None:
    repos = {"r": _repo_with_files(tmp_path, "r", {"AGENTS.md": "agent guide here"})}
    out = read_grounding_docs(repos)
    assert "## r (AGENTS.md)" in out
    assert "agent guide here" in out


def test_read_grounding_docs_prefers_claude_md_over_agents_md_when_both_exist(
    tmp_path: Path,
) -> None:
    repos = {
        "r": _repo_with_files(
            tmp_path, "r", {"CLAUDE.md": "claude wins", "AGENTS.md": "should be skipped"}
        )
    }
    out = read_grounding_docs(repos)
    assert "claude wins" in out
    assert "should be skipped" not in out
    assert "AGENTS.md" not in out


def test_read_grounding_docs_concatenates_one_per_repo_with_separators(
    tmp_path: Path,
) -> None:
    repos = {
        "api": _repo_with_files(tmp_path, "api", {"CLAUDE.md": "api conventions"}),
        "web": _repo_with_files(tmp_path, "web", {"AGENTS.md": "web conventions"}),
    }
    out = read_grounding_docs(repos)
    assert "## api (CLAUDE.md)" in out
    assert "## web (AGENTS.md)" in out
    assert "---" in out  # the separator between sections


def test_read_grounding_docs_truncates_files_above_the_size_cap(tmp_path: Path) -> None:
    huge = "x" * (GROUNDING_DOC_MAX_CHARS + 1000)
    repos = {"r": _repo_with_files(tmp_path, "r", {"CLAUDE.md": huge})}
    out = read_grounding_docs(repos)
    assert "[…truncated]" in out
    assert len(out) < GROUNDING_DOC_MAX_CHARS + 500


def test_read_grounding_docs_ignores_symlinked_claude_md_pointing_outside_repo(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("private contents that must not leak", encoding="utf-8")
    repos = {"r": _repo_with_files(tmp_path, "r", {"AGENTS.md": "real agents content"})}
    # Plant a symlinked CLAUDE.md pointing outside the repo root.
    (repos["r"].local_path / "CLAUDE.md").symlink_to(secret)

    out = read_grounding_docs(repos)

    assert "private contents" not in out
    # CLAUDE.md was rejected, so we fall through to AGENTS.md.
    assert "real agents content" in out
    assert "AGENTS.md" in out


def test_read_grounding_docs_ignores_in_tree_symlinks_too(tmp_path: Path) -> None:
    # Defense in depth — even an in-tree symlink is rejected so future stricter
    # checks (e.g. whitelisted relative paths) can't be defeated by aliasing.
    repos = {"r": _repo_with_files(tmp_path, "r", {"NOTES.md": "in-tree content"})}
    (repos["r"].local_path / "CLAUDE.md").symlink_to(repos["r"].local_path / "NOTES.md")

    out = read_grounding_docs(repos)

    assert out == ""


# --- refresh ----------------------------------------------------------------


import asyncio  # noqa: E402  (kept low to mirror the rest of the file's grouping)


def _capture_git_invocations(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    invocations: list[tuple[str, ...]] = []

    class _OkProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"")

    async def _fake_exec(*args: object, **_: object) -> _OkProc:
        invocations.append(tuple(str(a) for a in args))
        return _OkProc()

    monkeypatch.setattr(codebase_module.asyncio, "create_subprocess_exec", _fake_exec)
    return invocations


@pytest.mark.asyncio
async def test_refresh_repo_runs_fetch_then_hard_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invocations = _capture_git_invocations(monkeypatch)
    repo = Repo(name="api", url="git@example.com:x.git", local_path=tmp_path)

    assert await refresh_repo(repo) is True
    assert len(invocations) == 2
    assert invocations[0][:5] == ("git", "-C", str(tmp_path), "fetch", "--depth")
    assert invocations[1][:5] == ("git", "-C", str(tmp_path), "reset", "--hard")
    assert invocations[1][-1] == "FETCH_HEAD"


@pytest.mark.asyncio
async def test_refresh_repo_returns_false_when_git_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _FailProc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"force-pushed remote rejected")

    async def _fake_exec(*_args: object, **_kwargs: object) -> _FailProc:
        return _FailProc()

    monkeypatch.setattr(codebase_module.asyncio, "create_subprocess_exec", _fake_exec)
    repo = Repo(name="api", url="git@example.com:x.git", local_path=tmp_path)

    assert await refresh_repo(repo) is False


@pytest.mark.asyncio
async def test_run_refresh_loop_returns_immediately_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invocations = _capture_git_invocations(monkeypatch)
    repos = {"api": Repo(name="api", url="x", local_path=tmp_path)}
    locks = {"api": asyncio.Lock()}
    # interval <= 0 → loop returns without ever sleeping or fetching.
    await asyncio.wait_for(run_refresh_loop(repos, locks, 0), timeout=1.0)
    assert invocations == []


@pytest.mark.asyncio
async def test_run_refresh_loop_calls_fetch_and_reset_each_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invocations = _capture_git_invocations(monkeypatch)
    iterations = 0

    async def _fast_sleep(_: float) -> None:
        nonlocal iterations
        iterations += 1
        if iterations > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(codebase_module.asyncio, "sleep", _fast_sleep)
    repos = {
        "api": Repo(name="api", url="x", local_path=tmp_path / "api"),
        "web": Repo(name="web", url="y", local_path=tmp_path / "web"),
    }
    locks = {name: asyncio.Lock() for name in repos}

    with pytest.raises(asyncio.CancelledError):
        await run_refresh_loop(repos, locks, 1)

    # One iteration ran fetch + reset for each of the two repos.
    assert len(invocations) == 4
    verbs = {invocation[3] for invocation in invocations}
    assert verbs == {"fetch", "reset"}


# --- subprocess reap on timeout ---------------------------------------------


@pytest.mark.asyncio
async def test_refresh_repo_reaps_subprocess_after_kill_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Verify the kill+wait sequence: refresh that times out must call
    # proc.kill() AND proc.wait() so the child doesn't linger as a zombie
    # and the stdout/stderr pipes get closed.
    kill_called = False
    wait_called = False

    class _HangingProc:
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(3600)  # never returns; wait_for cancels us
            return (b"", b"")

        def kill(self) -> None:
            nonlocal kill_called
            kill_called = True
            self.returncode = -9

        async def wait(self) -> int:
            nonlocal wait_called
            wait_called = True
            return -9

    async def _fake_exec(*_args: object, **_kwargs: object) -> _HangingProc:
        return _HangingProc()

    monkeypatch.setattr(codebase_module.asyncio, "create_subprocess_exec", _fake_exec)
    # Compress the timeout so the test runs in milliseconds, not minutes.
    monkeypatch.setattr(codebase_module, "REFRESH_TIMEOUT_SECONDS", 0.05)

    repo = Repo(name="api", url="x", local_path=tmp_path)
    assert await refresh_repo(repo) is False
    assert kill_called, "timed-out subprocess must be killed"
    assert wait_called, "killed subprocess must be reaped via proc.wait()"


@pytest.mark.asyncio
async def test_clone_repo_reaps_subprocess_after_kill_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kill_called = False
    wait_called = False

    class _HangingProc:
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(3600)
            return (b"", b"")

        def kill(self) -> None:
            nonlocal kill_called
            kill_called = True
            self.returncode = -9

        async def wait(self) -> int:
            nonlocal wait_called
            wait_called = True
            return -9

    async def _fake_exec(*_args: object, **_kwargs: object) -> _HangingProc:
        return _HangingProc()

    monkeypatch.setattr(codebase_module.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(codebase_module, "CLONE_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(RuntimeError, match="git clone timed out"):
        await clone_repo(RepoSpec(name="api", url="x"), tmp_path)
    assert kill_called
    assert wait_called
