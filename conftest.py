"""Root pytest options shared by product and inherited legacy tests."""


def pytest_addoption(parser):
    """Register the upstream research options for root-level test collection."""
    options = {
        "--research-agent": "Agent type: multi_agent or graph",
        "--search-api": "Search API to use",
        "--eval-model": "Model for evaluation",
        "--supervisor-model": "Model for supervisor agent",
        "--researcher-model": "Model for researcher agent",
        "--planner-provider": "Provider for planner model",
        "--planner-model": "Model for planning",
        "--writer-provider": "Provider for writer model",
        "--writer-model": "Model for writing",
        "--max-search-depth": "Maximum search depth",
    }
    for name, help_text in options.items():
        parser.addoption(name, action="store", help=help_text)
