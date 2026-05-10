import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp
from PIL import Image
from pydantic_ai.messages import BinaryContent, UserContent

logger = logging.getLogger(__name__)

# Per-file cap. Slack itself accepts much larger uploads, but pulling 100MB
# blobs into a Slack-bot prompt isn't useful and risks blowing the LLM's
# context window or the OpenAI HTTP body limits.
MAX_FILE_BYTES = 10 * 1024 * 1024

# Anthropic recommends ≤1568px on the longest edge for the best quality/cost
# trade-off; OpenAI vision is similar. Sending a raw 4K screenshot just burns
# input tokens for no quality win — downscale before handing to the model.
MAX_IMAGE_DIMENSION = 1568
JPEG_QUALITY = 85
_RESIZABLE_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})

# Files we'll inline as text when the mimetype isn't text/*. Slack often
# reports `application/octet-stream` for source code uploads, so an extension
# fallback is the difference between "Pythia reads my .py" and "Pythia ignores
# my .py".
_TEXT_EXTENSIONS = (
    ".c",
    ".conf",
    ".cpp",
    ".css",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
)


@dataclass(frozen=True)
class FileAttachment:
    name: str
    mimetype: str
    data: bytes


def extract_file_metas(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk a thread's messages and pull out every file metadata blob Slack
    attached to them. Files without a download URL (e.g. external links) are
    skipped — there's nothing for us to fetch.
    """
    metas: list[dict[str, Any]] = []
    for message in messages:
        for f in message.get("files") or []:
            if f.get("url_private_download") or f.get("url_private"):
                metas.append(f)
    return metas


async def download_file(meta: dict[str, Any], bot_token: str) -> FileAttachment | None:
    url = meta.get("url_private_download") or meta.get("url_private")
    if not url:
        return None
    name = str(meta.get("name") or "file")
    mimetype = str(meta.get("mimetype") or "application/octet-stream")
    size = int(meta.get("size") or 0)
    if size > MAX_FILE_BYTES:
        logger.info("skipping oversized Slack file %s (%d bytes)", name, size)
        return None
    headers = {"Authorization": f"Bearer {bot_token}"}
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, headers=headers) as response,
        ):
            response.raise_for_status()
            data = await response.read()
    except Exception:
        logger.exception("failed to download Slack file %s", name)
        return None
    return FileAttachment(name=name, mimetype=mimetype, data=data)


def _has_transparency(image: Image.Image) -> bool:
    if image.mode in ("RGBA", "LA"):
        return True
    return image.mode == "P" and "transparency" in image.info


def downscale_image(data: bytes, mimetype: str) -> tuple[bytes, str]:
    """Shrink large raster images so we don't burn input tokens on pixels the
    model would just downsample anyway.

    - Skips formats Pillow can't safely round-trip (e.g. SVG).
    - Returns input unchanged if both edges are already within the cap.
    - Re-encodes to JPEG (quality 85) when there's no transparency to save
      bytes; preserves PNG when transparency would be lost otherwise.
    """
    if mimetype not in _RESIZABLE_IMAGE_TYPES:
        return data, mimetype
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        logger.exception("could not parse image for downscaling, sending as-is")
        return data, mimetype

    width, height = image.size
    longest = max(width, height)
    if longest <= MAX_IMAGE_DIMENSION:
        return data, mimetype

    scale = MAX_IMAGE_DIMENSION / longest
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    resized = image.resize(new_size, Image.Resampling.LANCZOS)

    out = io.BytesIO()
    if _has_transparency(image):
        if resized.mode != "RGBA":
            resized = resized.convert("RGBA")
        resized.save(out, format="PNG", optimize=True)
        new_mimetype = "image/png"
    else:
        if resized.mode != "RGB":
            resized = resized.convert("RGB")
        resized.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        new_mimetype = "image/jpeg"

    new_data = out.getvalue()
    logger.info(
        "downscaled image %dx%d→%dx%d (%d→%d bytes)",
        width,
        height,
        new_size[0],
        new_size[1],
        len(data),
        len(new_data),
    )
    return new_data, new_mimetype


def to_user_content(attachment: FileAttachment) -> UserContent | None:
    """Convert a Slack attachment into something PydanticAI can attach to a
    user prompt. Images become BinaryContent (passed straight to vision-capable
    models, downscaled if oversized). Text-like files are inlined as a fenced
    code block string. Other binary types (PDF, docx, …) return None —
    operators can hook in a converter MCP if they need them.
    """
    if attachment.mimetype.startswith("image/"):
        data, mimetype = downscale_image(attachment.data, attachment.mimetype)
        return BinaryContent(data=data, media_type=mimetype)
    is_text_mime = attachment.mimetype.startswith("text/") or attachment.mimetype in (
        "application/json",
        "application/xml",
        "application/yaml",
    )
    if is_text_mime or attachment.name.lower().endswith(_TEXT_EXTENSIONS):
        try:
            text = attachment.data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return f"\n\n[attached file: {attachment.name}]\n```\n{text}\n```"
    return None
