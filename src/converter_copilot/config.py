"""Environment-backed runtime configuration with opt-in, secret-safe tracing."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime settings needed by the copilot server and evaluation CLI."""

    openai_api_key: str | None
    model: str
    langsmith_api_key: str | None
    langsmith_project: str | None
    tracing_enabled: bool

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        """Load `.env` and snapshot only the settings used by the runtime."""
        load_dotenv()
        tracing_requested = _as_bool(os.getenv("LANGSMITH_TRACING"))
        langsmith_api_key = os.getenv("LANGSMITH_API_KEY")
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("CONVERTER_MODEL", "gpt-4.1-mini"),
            langsmith_api_key=langsmith_api_key,
            langsmith_project=os.getenv("LANGSMITH_PROJECT"),
            tracing_enabled=tracing_requested and bool(langsmith_api_key),
        )

    def apply_tracing_environment(self) -> bool:
        """Enable LangSmith only when explicitly requested and credentials exist."""
        if not self.tracing_enabled:
            return False
        os.environ["LANGSMITH_TRACING"] = "true"
        if self.langsmith_project:
            os.environ["LANGSMITH_PROJECT"] = self.langsmith_project
        return True


def tracing_run_config(*, task_id: str, mode: str) -> dict:
    """Return non-sensitive task and mode tags for LangChain/LangGraph runs."""
    return {
        "run_name": f"converter-{mode}",
        "tags": ["converter-copilot", f"task:{task_id}", f"mode:{mode}"],
        "metadata": {"task_id": task_id, "mode": mode},
    }
