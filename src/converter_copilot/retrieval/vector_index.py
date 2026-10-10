"""Persistent Chroma vector index for local source documents."""

import os
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from converter_copilot.domain.schemas import DocumentChunk, RetrievedChunk


class VectorIndex:
    """Chroma-backed index with injectable embeddings for offline tests."""

    def __init__(self, persist_directory: str | Path, embedding_function: Embeddings | None = None):
        self.persist_directory = str(persist_directory)
        if embedding_function is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("Set OPENAI_API_KEY or inject an embedding function to use the vector index.")
            embedding_function = OpenAIEmbeddings(
                model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
                api_key=api_key,
            )
        self._store = Chroma(
            collection_name="converter_engineering_copilot",
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
