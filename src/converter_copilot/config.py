"""Environment-backed runtime configuration with opt-in, secret-safe tracing."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime settings needed by the copilot server and evaluation CLI."""

    deepseek_api_key: str | None
    base_url: str
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
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=os.getenv("CONVERTER_MODEL", "deepseek-flash"),
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


@dataclass(frozen=True)
class WorkerSettings:
    """Validated settings for the local durable task worker."""

    concurrency: int = 1
    poll_seconds: float = 0.5
    lease_seconds: int = 60
    timeout_seconds: int = 1800
    max_attempts: int = 3

    def __post_init__(self) -> None:
        """Reject zero, negative, or non-finite worker limits."""
        import math

        if self.concurrency <= 0 or self.lease_seconds <= 0 or self.timeout_seconds <= 0 or self.max_attempts <= 0:
            raise ValueError("Worker concurrency, lease, timeout, and attempt limits must be positive.")
        if not math.isfinite(self.poll_seconds) or self.poll_seconds <= 0:
            raise ValueError("Worker poll interval must be a positive finite number.")

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        """Read worker limits from environment variables and validate them."""
        return cls(
            concurrency=int(os.getenv("CONVERTER_WORKER_CONCURRENCY", "1")),
            poll_seconds=float(os.getenv("CONVERTER_WORKER_POLL_SECONDS", "0.5")),
            lease_seconds=int(os.getenv("CONVERTER_TASK_LEASE_SECONDS", "60")),
            timeout_seconds=int(os.getenv("CONVERTER_TASK_TIMEOUT_SECONDS", "1800")),
            max_attempts=int(os.getenv("CONVERTER_TASK_MAX_ATTEMPTS", "3")),
        )


def tracing_run_config(*, task_id: str, mode: str) -> dict:
    """Return non-sensitive task and mode tags for LangChain/LangGraph runs."""
    return {
        "run_name": f"converter-{mode}",
        "tags": ["converter-copilot", f"task:{task_id}", f"mode:{mode}"],
        "metadata": {"task_id": task_id, "mode": mode},
    }
