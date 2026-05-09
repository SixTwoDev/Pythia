import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest

from pythia.codebase import (
    Repo,
    RepoSpec,
    build_codebase_tools,
    parse_repos,
    require_binaries,
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
