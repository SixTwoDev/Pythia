from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pydantic_ai.messages import BinaryContent

from pythia import slack_files as slack_files_module
from pythia.slack_files import (
    FileAttachment,
    download_file,
    extract_file_metas,
    to_user_content,
)


def test_extract_file_metas_returns_files_with_a_download_url() -> None:
    messages = [
        {"text": "look", "files": [{"id": "F1", "url_private_download": "https://x/a"}]},
        {"text": "and"},
        {"text": "this too", "files": [{"id": "F2", "url_private": "https://x/b"}]},
        {"text": "no url", "files": [{"id": "F3"}]},  # no URL — skipped
    ]
    metas = extract_file_metas(messages)
    assert [m["id"] for m in metas] == ["F1", "F2"]


def test_extract_file_metas_handles_messages_without_a_files_key() -> None:
    assert extract_file_metas([{"text": "hi"}, {"text": "hello"}]) == []


@pytest.mark.asyncio
async def test_download_file_returns_attachment_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    payload = b"hello bytes"

    class _FakeResp:
        async def __aenter__(self) -> "_FakeResp":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def read(self) -> bytes:
            return payload

    class _FakeSession:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeSession":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def get(self, url: str, headers: dict[str, str]) -> _FakeResp:
            captured["url"] = url
            captured["headers"] = headers
            return _FakeResp()

    monkeypatch.setattr(slack_files_module.aiohttp, "ClientSession", _FakeSession)
    meta = {
        "url_private_download": "https://files.slack.com/x",
        "name": "log.txt",
        "mimetype": "text/plain",
        "size": len(payload),
    }
    attachment = await download_file(meta, "xoxb-test")

    assert attachment == FileAttachment(name="log.txt", mimetype="text/plain", data=payload)
    assert captured["url"] == "https://files.slack.com/x"
    assert captured["headers"] == {"Authorization": "Bearer xoxb-test"}


@pytest.mark.asyncio
async def test_download_file_returns_none_when_oversized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If the size hint is over the cap we should never even call out — fail
    # the test if aiohttp gets touched.
    sentinel: Callable[..., Awaitable[Any]] = pytest.fail  # type: ignore[assignment]
    monkeypatch.setattr(slack_files_module.aiohttp, "ClientSession", sentinel)
    meta = {"url_private_download": "https://x/y", "name": "huge.bin", "size": 99 * 1024 * 1024}
    assert await download_file(meta, "xoxb-test") is None


@pytest.mark.asyncio
async def test_download_file_returns_none_when_no_url() -> None:
    assert await download_file({"name": "x"}, "xoxb-test") is None


def test_to_user_content_wraps_images_as_binary_content() -> None:
    att = FileAttachment(name="screen.png", mimetype="image/png", data=b"\x89PNG...")
    content = to_user_content(att)
    assert isinstance(content, BinaryContent)
    assert content.media_type == "image/png"
    assert content.data == b"\x89PNG..."


def test_to_user_content_inlines_text_files_as_fenced_code_blocks() -> None:
    att = FileAttachment(name="error.log", mimetype="text/plain", data=b"line one\nline two")
    content = to_user_content(att)
    assert isinstance(content, str)
    assert "[attached file: error.log]" in content
    assert "line one" in content
    assert content.strip().endswith("```")


def test_to_user_content_inlines_source_code_via_extension_fallback() -> None:
    att = FileAttachment(name="util.py", mimetype="application/octet-stream", data=b"print(1)")
    content = to_user_content(att)
    assert isinstance(content, str)
    assert "[attached file: util.py]" in content
    assert "print(1)" in content


def test_to_user_content_returns_none_for_unsupported_binary_types() -> None:
    att = FileAttachment(name="report.pdf", mimetype="application/pdf", data=b"%PDF-1.4...")
    assert to_user_content(att) is None


def test_to_user_content_returns_none_for_undecodable_text() -> None:
    att = FileAttachment(name="weird.log", mimetype="text/plain", data=b"\xff\xfe\xfd not utf-8")
    assert to_user_content(att) is None
