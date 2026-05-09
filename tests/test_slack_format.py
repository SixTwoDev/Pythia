from pythia.slack_format import to_slack_mrkdwn


def test_double_asterisk_bold_becomes_single_asterisk_bold() -> None:
    assert to_slack_mrkdwn("**hello**") == "*hello*"


def test_double_underscore_bold_also_becomes_single_asterisk() -> None:
    assert to_slack_mrkdwn("__hello__") == "*hello*"


def test_strikethrough_collapses_double_tildes_to_single() -> None:
    assert to_slack_mrkdwn("~~old~~") == "~old~"


def test_markdown_links_become_slack_link_syntax() -> None:
    assert to_slack_mrkdwn("[Slack](https://slack.com)") == "<https://slack.com|Slack>"


def test_headings_at_any_level_become_bold_lines() -> None:
    assert to_slack_mrkdwn("# Top") == "*Top*"
    assert to_slack_mrkdwn("## Section") == "*Section*"
    assert to_slack_mrkdwn("###### Deep") == "*Deep*"


def test_dash_and_star_bullets_become_unicode_bullets() -> None:
    assert to_slack_mrkdwn("- one\n- two") == "• one\n• two"
    assert to_slack_mrkdwn("* one\n* two") == "• one\n• two"


def test_indented_bullets_keep_their_indentation() -> None:
    assert to_slack_mrkdwn("  - nested") == "  • nested"


def test_bold_inside_a_bullet_is_converted_correctly() -> None:
    assert to_slack_mrkdwn("- **bold item**") == "• *bold item*"


def test_fenced_code_block_contents_are_left_untouched() -> None:
    src = "before\n```python\n**not_bold**\n[not_a_link](url)\n```\nafter"
    assert to_slack_mrkdwn(src) == src


def test_inline_code_contents_are_left_untouched() -> None:
    assert to_slack_mrkdwn("Use `**raw**` here") == "Use `**raw**` here"


def test_real_world_llm_response_converts_cleanly() -> None:
    src = (
        "## Findings\n"
        "- The handler lives in `src/pythia/app.py`.\n"
        "- It uses **PydanticAI** to call the [LLM](https://example.com).\n\n"
        "```python\nawait answer(agent, prompt)\n```"
    )
    expected = (
        "*Findings*\n"
        "• The handler lives in `src/pythia/app.py`.\n"
        "• It uses *PydanticAI* to call the <https://example.com|LLM>.\n\n"
        "```python\nawait answer(agent, prompt)\n```"
    )
    assert to_slack_mrkdwn(src) == expected
