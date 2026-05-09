from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel

from pythia.agent import DEFAULT_SYSTEM_PROMPT, _system_prompt, answer, build_agent
from pythia.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "slack_bot_token": "xoxb-test",
        "slack_app_token": "xapp-test",
        "openai_api_key": "sk-test",
        "openai_model": "openai/gpt-4o-mini",
    }
    base.update(overrides)
    return Settings.model_validate(base)


def test_default_system_prompt_used_when_no_file_configured() -> None:
    assert _system_prompt(_settings()) == DEFAULT_SYSTEM_PROMPT


def test_system_prompt_loaded_from_file_when_configured(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("custom prompt body", encoding="utf-8")
    settings = _settings(pythia_system_prompt_file=str(prompt_file))
    assert _system_prompt(settings) == "custom prompt body"


def test_build_agent_returns_an_agent_using_the_configured_model() -> None:
    agent = build_agent(_settings(openai_model="openai/gpt-4o"))
    assert isinstance(agent, Agent)
    assert isinstance(agent.model, OpenAIChatModel)
    assert agent.model.model_name == "openai/gpt-4o"


@pytest.mark.asyncio
async def test_answer_runs_the_agent_and_returns_its_output_as_a_string() -> None:
    agent = build_agent(_settings())
    with agent.override(model=TestModel(custom_output_text="hello from pythia")):
        reply = await answer(agent, "any prompt")
    assert reply == "hello from pythia"
