import io
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from PIL import Image
from pydantic_ai.messages import BinaryContent

from pythia import slack_files as slack_files_module
from pythia.slack_files import (
    MAX_IMAGE_DIMENSION,
    FileAttachment,
    download_file,
    downscale_image,
    extract_file_metas,
    to_user_content,
)


def _png_bytes(size: tuple[int, int], mode: str = "RGB") -> bytes:
    color = (120, 130, 140) if mode == "RGB" else (120, 130, 140, 200)
    img = Image.new(mode, size, color=color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _jpeg_bytes(size: tuple[int, int]) -> bytes:
    img = Image.new("RGB", size, color=(50, 100, 150))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


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


def _install_fake_aiohttp(
    monkeypatch: pytest.MonkeyPatch, chunks: list[bytes], captured: dict[str, Any] | None = None
) -> None:
    """Patch aiohttp.ClientSession with a fake that yields the given chunks
    from response.content.iter_chunked() — the streaming path the download
    code uses to enforce a memory-bounded cap."""
    sink: dict[str, Any] = captured if captured is not None else {}

    class _FakeContent:
        async def iter_chunked(self, _size: int) -> Any:
            for chunk in chunks:
                yield chunk

    class _FakeResp:
        content = _FakeContent()

        async def __aenter__(self) -> "_FakeResp":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

    class _FakeSession:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeSession":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def get(self, url: str, headers: dict[str, str]) -> _FakeResp:
            sink["url"] = url
            sink["headers"] = headers
            return _FakeResp()

    monkeypatch.setattr(slack_files_module.aiohttp, "ClientSession", _FakeSession)


@pytest.mark.asyncio
async def test_download_file_returns_attachment_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    payload = b"hello bytes"
    _install_fake_aiohttp(monkeypatch, [payload], captured)
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
async def test_download_file_aborts_when_streamed_bytes_exceed_cap_despite_size_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Slack metadata claims the file is small, but the actual stream is huge.
    # The streaming cap must catch this even though the pre-flight size check
    # passed — that's the whole point of bounding memory at read time.
    over_cap = slack_files_module.MAX_FILE_BYTES + 1024
    chunks = [b"x" * (256 * 1024) for _ in range((over_cap // (256 * 1024)) + 1)]
    _install_fake_aiohttp(monkeypatch, chunks)
    meta = {
        "url_private_download": "https://files.slack.com/x",
        "name": "lying.bin",
        "mimetype": "application/octet-stream",
        "size": 100,  # blatant lie — actual stream is much larger
    }
    assert await download_file(meta, "xoxb-test") is None


@pytest.mark.asyncio
async def test_download_file_handles_missing_size_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If Slack omits the size field entirely, the pre-flight check would
    # pass (treats missing as 0); the streaming cap is the only thing
    # standing between us and an unbounded download.
    over_cap = slack_files_module.MAX_FILE_BYTES + 1024
    chunks = [b"y" * (512 * 1024) for _ in range((over_cap // (512 * 1024)) + 1)]
    _install_fake_aiohttp(monkeypatch, chunks)
    meta = {
        "url_private_download": "https://files.slack.com/x",
        "name": "no-size.bin",
        "mimetype": "application/octet-stream",
        # no `size` key at all
    }
    assert await download_file(meta, "xoxb-test") is None


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


# --- image downscaling ------------------------------------------------------


def test_downscale_image_passes_small_images_through_unchanged() -> None:
    original = _jpeg_bytes((800, 600))
    data, mimetype = downscale_image(original, "image/jpeg")
    assert data == original
    assert mimetype == "image/jpeg"


def test_downscale_image_shrinks_large_images_to_fit_max_dimension() -> None:
    original = _jpeg_bytes((4000, 3000))
    data, mimetype = downscale_image(original, "image/jpeg")
    out = Image.open(io.BytesIO(data))
    assert max(out.size) <= MAX_IMAGE_DIMENSION
    width, height = out.size
    assert abs((width / height) - (4000 / 3000)) < 0.01  # aspect ratio preserved
    assert mimetype == "image/jpeg"
    assert len(data) < len(original)


def test_downscale_image_keeps_png_when_source_has_transparency() -> None:
    original = _png_bytes((3000, 3000), mode="RGBA")
    data, mimetype = downscale_image(original, "image/png")
    assert mimetype == "image/png"
    out = Image.open(io.BytesIO(data))
    assert out.mode in ("RGBA", "LA", "P")
    assert max(out.size) <= MAX_IMAGE_DIMENSION


def test_downscale_image_converts_opaque_png_to_jpeg_for_byte_savings() -> None:
    original = _png_bytes((3000, 3000), mode="RGB")
    data, mimetype = downscale_image(original, "image/png")
    assert mimetype == "image/jpeg"
    assert len(data) < len(original)


def test_downscale_image_returns_input_unchanged_for_unsupported_types() -> None:
    raw = b"<svg></svg>"
    data, mimetype = downscale_image(raw, "image/svg+xml")
    assert data == raw
    assert mimetype == "image/svg+xml"


def test_downscale_image_returns_input_unchanged_when_pillow_cant_parse() -> None:
    raw = b"not a real image"
    data, mimetype = downscale_image(raw, "image/jpeg")
    assert data == raw
    assert mimetype == "image/jpeg"


def test_to_user_content_downscales_oversized_images_before_wrapping() -> None:
    original = _jpeg_bytes((3000, 2000))
    att = FileAttachment(name="screen.jpg", mimetype="image/jpeg", data=original)
    content = to_user_content(att)
    assert isinstance(content, BinaryContent)
    out = Image.open(io.BytesIO(content.data))
    assert max(out.size) <= MAX_IMAGE_DIMENSION
