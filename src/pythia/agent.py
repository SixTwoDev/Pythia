from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from pythia.config import Settings
from pythia.mcp_servers import load_mcp_servers

DEFAULT_SYSTEM_PROMPT = """\
You are Pythia, an assistant in a Slack workspace. You help engineers investigate questions \
by reasoning over the conversation and any tools available to you.

You will receive the contents of a Slack thread. The most recent message is the one that \
mentioned you; earlier messages are context. Reply concisely in Slack-flavoured markdown.

When you cite a fact (a Jira ticket, a metric, a code path, a log line), include the source \
so the reader can verify. When you are uncertain, say so plainly.\
"""


def _system_prompt(settings: Settings) -> str:
    if settings.pythia_system_prompt_file:
        return Path(settings.pythia_system_prompt_file).read_text(encoding="utf-8")
    return DEFAULT_SYSTEM_PROMPT


def build_agent(settings: Settings) -> Agent[None, str]:
    provider = OpenAIProvider(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    model = OpenAIChatModel(settings.openai_model, provider=provider)
    return Agent(
        model,
        system_prompt=_system_prompt(settings),
        toolsets=load_mcp_servers(settings.mcp_servers_config),
    )


async def answer(agent: Agent[None, str], prompt: str) -> str:
    result = await agent.run(prompt)
    return str(result.output)
