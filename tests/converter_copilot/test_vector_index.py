from langchain_core.embeddings import Embeddings

from converter_copilot.domain.schemas import DocumentChunk
from converter_copilot.retrieval.vector_index import VectorIndex


class FakeEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        terms = text.lower()
        return [float("母线" in terms), float("过压" in terms), float("电容" in terms), 1.0]


def _chunk(chunk_id, document_id, filename, text, locator):
    return DocumentChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        filename=filename,
        text=text,
        locator=locator,
        metadata={"kind": "manual" if filename.endswith("pdf") else "case"},
    )


def test_add_search_filter_and_delete(tmp_path):
    index = VectorIndex(tmp_path / "chroma", embedding_function=FakeEmbeddings())
    index.add(
        [
            _chunk("c1", "d1", "manual.pdf", "直流母线过压，检查电容。", "page 4"),
            _chunk("c2", "d2", "case.md", "风扇故障导致温升。", "heading: 温升"),
        ]
    )
    results = index.search("直流母线过压", limit=2, filters={"kind": "manual"})
    assert [item.chunk_id for item in results] == ["c1"]
    assert results[0].locator == "page 4"
    assert results[0].score > 0
    index.delete_document("d1")
    assert all(item.document_id != "d1" for item in index.search("直流母线过压", limit=2))


def test_persistent_index_survives_recreation(tmp_path):
    directory = tmp_path / "chroma"
    first = VectorIndex(directory, embedding_function=FakeEmbeddings())
    first.add([_chunk("c1", "d1", "manual.pdf", "直流母线过压。", "page 2")])
    del first

    second = VectorIndex(directory, embedding_function=FakeEmbeddings())
    result = second.search("直流母线过压", limit=1)
    assert result[0].chunk_id == "c1"
    assert result[0].document_id == "d1"


def test_contains_and_exact_chunk_delete(tmp_path):
    index = VectorIndex(tmp_path / "chroma", embedding_function=FakeEmbeddings())
    index.add([
        _chunk("c1", "d1", "manual.pdf", "直流母线过压。", "page 1"),
        _chunk("c2", "d2", "other.pdf", "检查电容。", "page 2"),
    ])

    assert index.contains_chunks(["c1", "c2"])
    index.delete_chunks(["c1"])

    assert not index.contains_chunks(["c1"])
    assert index.contains_chunks(["c2"])
