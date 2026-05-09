import re

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
