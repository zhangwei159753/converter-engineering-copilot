"""LangGraph Studio and production dependency entry points."""

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch

from converter_copilot.agent.workflows import WorkflowDeps, build_workflow
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


def build_default_deps(config: dict | None = None) -> WorkflowDeps:
    """Create production dependencies from server environment or explicit config."""
    load_dotenv()
    config = config or {}
    api_key = config.get("openai_api_key") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required to run a converter workflow.")
    model = ChatOpenAI(
        model=config.get("model") or os.getenv("CONVERTER_MODEL", "gpt-4.1-mini"),
        temperature=0,
        api_key=api_key,
    )
    return WorkflowDeps(
        model=model,
        search=TavilySearchAdapter() if os.getenv("TAVILY_API_KEY") else EmptySearchAdapter(),
        vector_index=VectorIndex(os.getenv("CHROMA_PERSIST_DIR", ".data/chroma")),
    )


converter_copilot = build_workflow(TaskMode.FAULT_ANALYSIS, build_default_deps())
