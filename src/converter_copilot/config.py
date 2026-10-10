"""Environment-backed runtime configuration with opt-in, secret-safe tracing."""

import os
from dataclasses import dataclass
from pathlib import Path

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
        load_dotenv()
        return cls(
            concurrency=int(os.getenv("CONVERTER_WORKER_CONCURRENCY", "1")),
            poll_seconds=float(os.getenv("CONVERTER_WORKER_POLL_SECONDS", "0.5")),
            lease_seconds=int(os.getenv("CONVERTER_TASK_LEASE_SECONDS", "60")),
            timeout_seconds=int(os.getenv("CONVERTER_TASK_TIMEOUT_SECONDS", "1800")),
            max_attempts=int(os.getenv("CONVERTER_TASK_MAX_ATTEMPTS", "3")),
        )


@dataclass(frozen=True)
class ImportLimits:
    """Validated local upload ceilings applied before import side effects."""

    max_bytes: int = 20 * 1024 * 1024
    max_pdf_pages: int = 200
    max_csv_rows: int = 10_000
    max_chunks: int = 2_000

    def __post_init__(self) -> None:
        """Reject limits that would disable bounded import processing."""
        if min(self.max_bytes, self.max_pdf_pages, self.max_csv_rows, self.max_chunks) <= 0:
            raise ValueError("Import limits must be positive.")

    @classmethod
    def from_env(cls) -> "ImportLimits":
        """Load bounded upload limits from the local environment."""
        load_dotenv()
        return cls(
            max_bytes=int(os.getenv("IMPORT_MAX_BYTES", str(20 * 1024 * 1024))),
            max_pdf_pages=int(os.getenv("IMPORT_MAX_PDF_PAGES", "200")),
            max_csv_rows=int(os.getenv("IMPORT_MAX_CSV_ROWS", "10000")),
            max_chunks=int(os.getenv("IMPORT_MAX_CHUNKS", "2000")),
        )


def import_source_directory(default: str | Path | None = None) -> Path:
    """Return the local directory used to retain validated original uploads."""
    load_dotenv()
    return Path(os.getenv("CONVERTER_SOURCE_DIR", str(default or ".data/sources")))


def tracing_run_config(*, task_id: str, mode: str) -> dict:
    """Return non-sensitive task and mode tags for LangChain/LangGraph runs."""
    return {
        "run_name": f"converter-{mode}",
        "tags": ["converter-copilot", f"task:{task_id}", f"mode:{mode}"],
        "metadata": {"task_id": task_id, "mode": mode},
    }
