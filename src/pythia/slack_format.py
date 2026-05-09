import re
from collections.abc import Sequence

# Slack's mrkdwn dialect differs from standard Markdown / CommonMark in a few
# ways that LLM output trips over: *bold* uses single asterisks (not **bold**),
# links are <url|text> (not [text](url)), there are no # headings, and bullets
# render as • (not - or *). This module does the minimum-viable conversion —
# enough to make typical LLM output render properly in Slack.

_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD_UNDERSCORE = re.compile(r"__(.+?)__", re.DOTALL)
_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_BULLET = re.compile(r"^([ \t]*)[*-]\s+", re.MULTILINE)

_FENCED_CODE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE = re.compile(r"`[^`\n]+?`")


def to_slack_mrkdwn(text: str) -> str:
    """Convert standard Markdown to Slack's mrkdwn dialect.

    Code spans (fenced and inline) are extracted before transformation and
    restored after, so that `**not bold**` inside a code block stays literal.
    """
    stashed: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        stashed.append(match.group(0))
        return f"\x00CODE{len(stashed) - 1}\x00"

    text = _FENCED_CODE.sub(_stash, text)
    text = _INLINE_CODE.sub(_stash, text)

    text = _BOLD.sub(r"*\1*", text)
    text = _BOLD_UNDERSCORE.sub(r"*\1*", text)
    text = _STRIKE.sub(r"~\1~", text)
    text = _LINK.sub(r"<\2|\1>", text)
    text = _HEADING.sub(r"*\1*", text)
    text = _BULLET.sub(r"\1• ", text)

    for index, code in enumerate(stashed):
        text = text.replace(f"\x00CODE{index}\x00", code)

    return text


_MAX_TRACE_LINE = 120


def _sanitize_trace_segment(value: str) -> str:
    """Make a tool name or args string safe to embed in a fenced code block on
    a single line.

    - Newlines and tabs collapse to a single space so each call stays one line.
    - Triple backticks are split with zero-width spaces so the surrounding
      ```fence``` doesn't get closed early by an LLM-supplied arg.
    """
    cleaned = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return cleaned.replace("```", "`​`​`")


def format_tool_trace(calls: "Sequence[object]") -> str:
    """Render a list of ToolCall objects as plain `name(args)` lines.

    Each line is capped at ~120 chars so noisy MCP tools (long URLs, big
    JSON args) don't dominate the modal. Order preserved so the trace
    reads top-to-bottom in call order. Segments are sanitized so embedded
    newlines or backticks can't break the per-line / code-fence rendering.
    """
    lines: list[str] = []
    for call in calls:
        name = _sanitize_trace_segment(str(getattr(call, "name", "?")))
        args = _sanitize_trace_segment(str(getattr(call, "args", "")))
        line = f"{name}({args})"
        if len(line) > _MAX_TRACE_LINE:
            line = line[: _MAX_TRACE_LINE - 1] + "…"
        lines.append(line)
    return "\n".join(lines)
