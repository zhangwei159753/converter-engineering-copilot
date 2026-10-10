from types import SimpleNamespace

from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI

from converter_copilot import config as runtime_config
from converter_copilot.agent import entrypoint
from converter_copilot.domain.schemas import DocumentChunk
from converter_copilot.retrieval import vector_index


class FakeFastEmbedModel:
    def embed(self, texts):
        return ([float(len(texts)), float(len(text))] for text in texts)


class FakeEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[float(len(text)), 1.0] for text in texts]

    def embed_query(self, text):
        return [float(len(text)), 1.0]


def test_runtime_config_loads_deepseek_settings(monkeypatch):
    monkeypatch.setattr(runtime_config, "load_dotenv", lambda: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.delenv("CONVERTER_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)

    config = runtime_config.RuntimeConfig.from_env()

    assert config.deepseek_api_key == "deepseek-test-key"
    assert config.model == "deepseek-flash"
    assert config.base_url == "https://api.deepseek.com"


def test_local_fastembed_adapts_document_and_query_vectors():
    embeddings = vector_index.LocalFastEmbedEmbeddings(model=FakeFastEmbedModel())

    assert embeddings.embed_documents(["母线过压", "风扇故障"]) == [[2.0, 4.0], [2.0, 4.0]]
    assert embeddings.embed_query("母线") == [1.0, 2.0]


def test_build_default_deps_configures_deepseek_chat(monkeypatch):
    captured = {}

    class FakeVectorIndex:
        def __init__(self, *args, **kwargs):
            pass

    def make_chat_model(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        entrypoint.RuntimeConfig,
        "from_env",
        lambda: SimpleNamespace(
            deepseek_api_key=None,
            model="deepseek-flash",
            base_url="https://api.deepseek.com",
            apply_tracing_environment=lambda: False,
        ),
    )
    monkeypatch.setattr(entrypoint, "DeepSeekChatOpenAI", make_chat_model)
    monkeypatch.setattr(entrypoint, "VectorIndex", FakeVectorIndex)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    deps = entrypoint.build_default_deps({"deepseek_api_key": "deepseek-test-key"})

    assert deps.model is not None
    assert captured == {
        "model": "deepseek-flash",
        "temperature": 0,
        "api_key": "deepseek-test-key",
        "base_url": "https://api.deepseek.com",
        "extra_body": {"thinking": {"type": "disabled"}},
    }


def test_deepseek_structured_output_uses_function_calling(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ChatOpenAI,
        "with_structured_output",
        lambda self, schema=None, **kwargs: captured.update(kwargs),
    )
    model = entrypoint.DeepSeekChatOpenAI(
        model="deepseek-flash",
        api_key="deepseek-test-key",
        base_url="https://api.deepseek.com",
    )

    model.with_structured_output(dict)

    assert captured["method"] == "function_calling"


def test_default_local_index_uses_a_new_collection_without_an_openai_key(tmp_path, monkeypatch):
    directory = tmp_path / "chroma"

    class FakeLocalEmbeddings(FakeEmbeddings):
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(vector_index, "LocalFastEmbedEmbeddings", FakeLocalEmbeddings, raising=False)
    monkeypatch.setenv("CHROMA_COLLECTION_NAME", "converter_engineering_copilot_bge_small_zh_v1_5")

    previous_index = vector_index.VectorIndex(
        directory,
        embedding_function=FakeEmbeddings(),
        collection_name="converter_engineering_copilot",
    )
    previous_index.add(
        [
            DocumentChunk(
                chunk_id="old-chunk",
                document_id="old-document",
                filename="manual.md",
                text="直流母线过压",
                locator="heading: 母线",
                metadata={},
            )
        ]
    )

    local_index = vector_index.VectorIndex(directory)

    assert local_index.search("直流母线过压") == []
    assert [item.chunk_id for item in previous_index.search("直流母线过压")] == ["old-chunk"]
