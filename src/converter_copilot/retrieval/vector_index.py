"""Persistent Chroma vector index for local source documents."""

import os
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_EMBEDDING_CACHE = ".data/fastembed"
DEFAULT_COLLECTION_NAME = "converter_engineering_copilot_bge_small_zh_v1_5"


class LocalFastEmbedEmbeddings(Embeddings):
    """Generate local Chinese embeddings with FastEmbed's ONNX runtime."""

    def __init__(self, model_name: str | None = None, cache_dir: str | Path | None = None, model=None):
        if model is None:
            from fastembed import TextEmbedding

            model = TextEmbedding(
                model_name=model_name or os.getenv("FASTEMBED_MODEL", DEFAULT_EMBEDDING_MODEL),
                cache_dir=str(cache_dir or os.getenv("FASTEMBED_CACHE_DIR", DEFAULT_EMBEDDING_CACHE)),
            )
        self._model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._to_vector(vector) for vector in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    @staticmethod
    def _to_vector(vector) -> list[float]:
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        return [float(value) for value in vector]

from converter_copilot.domain.schemas import DocumentChunk, RetrievedChunk


class VectorIndex:
    """Chroma-backed index with injectable embeddings for offline tests."""

    def __init__(
        self,
        persist_directory: str | Path,
        embedding_function: Embeddings | None = None,
        collection_name: str | None = None,
    ):
        self.persist_directory = str(persist_directory)
        if embedding_function is None:
            embedding_function = LocalFastEmbedEmbeddings(
                model_name=os.getenv("FASTEMBED_MODEL", DEFAULT_EMBEDDING_MODEL),
                cache_dir=os.getenv("FASTEMBED_CACHE_DIR", DEFAULT_EMBEDDING_CACHE),
            )
        self.collection_name = collection_name or os.getenv("CHROMA_COLLECTION_NAME", DEFAULT_COLLECTION_NAME)
        self._store = Chroma(
            collection_name=self.collection_name,
            persist_directory=self.persist_directory,
            embedding_function=embedding_function,
        )

    def add(self, chunks: list[DocumentChunk]) -> None:
        """Upsert chunks and their source locators into persistent storage."""
        if not chunks:
            return
        documents = [
            Document(
                page_content=chunk.text,
                metadata={
                    **chunk.metadata,
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "filename": chunk.filename,
                    "locator": chunk.locator,
                },
            )
            for chunk in chunks
        ]
        self._store.add_documents(documents, ids=[chunk.chunk_id for chunk in chunks])

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        """Return the nearest chunks with a positive normalized relevance score."""
        if limit <= 0 or not query.strip():
            return []
        matches = self._store.similarity_search_with_score(query, k=limit, filter=filters or None)
        results = []
        for document, distance in matches:
            metadata = dict(document.metadata)
            results.append(
                RetrievedChunk(
                    chunk_id=metadata.pop("chunk_id"),
                    document_id=metadata.pop("document_id"),
                    filename=metadata.pop("filename"),
                    locator=metadata.pop("locator"),
                    text=document.page_content,
                    metadata=metadata,
                    score=1.0 / (1.0 + max(float(distance), 0.0)),
                )
            )
        return results

    def delete_document(self, document_id: str) -> None:
        """Delete every indexed chunk belonging to a source document."""
        self._store.delete(where={"document_id": document_id})
