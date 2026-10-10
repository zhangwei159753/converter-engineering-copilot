"""LangGraph Studio and production dependency entry points."""

import os

from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch

from converter_copilot.agent.workflows import WorkflowDeps, build_workflow
from converter_copilot.config import RuntimeConfig
from converter_copilot.domain.schemas import TaskMode
from converter_copilot.retrieval.vector_index import VectorIndex


class TavilySearchAdapter:
    """Adapt Tavily's tool result shape for workflow source normalization."""

    def __init__(self):
        self._tool = TavilySearch(max_results=5, search_depth="advanced", tavily_api_key=os.getenv("TAVILY_API_KEY"))

    def search(self, query: str, *, limit: int = 5):
        result = self._tool.invoke({"query": query})
        results = result if isinstance(result, list) else result.get("results", [])
        return results[:limit]


class EmptySearchAdapter:
    """No-op external retrieval when the optional Tavily key is not configured."""

    def search(self, query: str, *, limit: int = 5):
        return []


class DeepSeekChatOpenAI(ChatOpenAI):
    """Configure LangChain's structured responses for DeepSeek tool calling."""

    def with_structured_output(self, schema=None, *, method="function_calling", **kwargs):
        return super().with_structured_output(schema, method=method, **kwargs)


def build_default_deps(config: dict | None = None) -> WorkflowDeps:
    """Create production dependencies from server environment or explicit config."""
    runtime = RuntimeConfig.from_env()
    config = config or {}
    api_key = config.get("deepseek_api_key") or runtime.deepseek_api_key
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required to run a converter workflow.")
    runtime.apply_tracing_environment()
    model = DeepSeekChatOpenAI(
        model=config.get("model") or runtime.model,
        temperature=0,
        api_key=api_key,
        base_url=config.get("base_url") or runtime.base_url,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return WorkflowDeps(
        model=model,
        search=TavilySearchAdapter() if os.getenv("TAVILY_API_KEY") else EmptySearchAdapter(),
        vector_index=VectorIndex(
            os.getenv("CHROMA_PERSIST_DIR", ".data/chroma"),
            collection_name=os.getenv(
                "CHROMA_COLLECTION_NAME",
                "converter_engineering_copilot_bge_small_zh_v1_5",
            ),
        ),
    )


def build_converter_copilot():
    """Construct the configured default graph when LangGraph loads this entry point."""
    return build_workflow(TaskMode.FAULT_ANALYSIS, build_default_deps())
