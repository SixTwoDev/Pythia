from pathlib import Path

import pytest

# Pythia env vars Settings reads. Cleared per-test so a developer's local
# .env (which Settings would otherwise pick up via its env_file config)
# never leaks into the test suite.
_PYTHIA_ENV_VARS = (
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "PYTHIA_SYSTEM_PROMPT_FILE",
    "MCP_SERVERS_CONFIG",
    "CODEBASE_REPOS",
)


@pytest.fixture(autouse=True)
def _isolate_pythia_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in _PYTHIA_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)  # so Settings's env_file=".env" never finds the dev's file
