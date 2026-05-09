import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp
from pydantic_ai.messages import BinaryContent, UserContent

logger = logging.getLogger(__name__)

# Per-file cap. Slack itself accepts much larger uploads, but pulling 100MB
# blobs into a Slack-bot prompt isn't useful and risks blowing the LLM's
# context window or the OpenAI HTTP body limits.
MAX_FILE_BYTES = 10 * 1024 * 1024

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


def to_user_content(attachment: FileAttachment) -> UserContent | None:
    """Convert a Slack attachment into something PydanticAI can attach to a
    user prompt. Images become BinaryContent (passed straight to vision-capable
    models). Text-like files are inlined as a fenced code block string. Other
    binary types (PDF, docx, …) return None — operators can hook in a
    converter MCP if they need them.
    """
    if attachment.mimetype.startswith("image/"):
        return BinaryContent(data=attachment.data, media_type=attachment.mimetype)
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
