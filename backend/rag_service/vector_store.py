"""
NeuraFleet RAG Vector Store
============================

Manages a ChromaDB collection of robot fleet documentation.
Uses ChromaDB's default ONNX embedder (all-MiniLM-L6-v2) for embeddings
and cosine similarity for retrieval.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

logger = logging.getLogger("rag_service.vector_store")

# Directory where persistent ChromaDB data lives
_CHROMA_DIR = os.getenv("CHROMA_DATA_DIR", "./chroma_data")

# Path to sample robot documentation
_DOCS_DIR = Path(__file__).parent / "data" / "robot_docs"


class VectorStore:
    """
    Wraps ChromaDB with a single ``robot_docs`` collection.

    Embeddings are computed by ChromaDB's built-in default (ONNX) embedding function
    integration (model: all-MiniLM-L6-v2).
    """

    def __init__(self, persist_dir: str = _CHROMA_DIR, embedding_function=None):
        persist_dir = os.path.abspath(persist_dir)
        logger.info("Initialising ChromaDB (persist=%s)", persist_dir)
        # PersistentClient is the only client that actually writes to disk;
        # chromadb.Client() is ephemeral and ``chroma_db_impl`` was removed in 0.4.
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        # Get or create the collection
        extra = {"embedding_function": embedding_function} if embedding_function else {}
        self._collection = self._client.get_or_create_collection(
            name="robot_docs",
            metadata={"hnsw:space": "cosine"},
            **extra,
        )
        logger.info(
            "Collection 'robot_docs' ready (%d documents)",
            self._collection.count(),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_documents(self, documents: list[dict]) -> int:
        """
        Add documents to the collection.

        Each dict should have:
          - ``text`` (str): the document content
          - ``metadata`` (dict, optional): e.g. source, robot_type, category

        Returns the number of documents added.
        """
        if not documents:
            return 0

        # Content-addressed IDs: re-ingesting the same document is a no-op, and two
        # different documents can never share an ID.  (The old ``doc_{count + i}`` scheme
        # silently overwrote existing documents after any delete or second /ingest.)
        batch: dict[str, tuple[str, dict | None]] = {}
        for doc in documents:
            metadata = doc.get("metadata") or None  # Chroma rejects {}
            batch[self._doc_id(doc["text"], metadata)] = (doc["text"], metadata)

        before = self._collection.count()
        self._collection.upsert(
            ids=list(batch),
            documents=[text for text, _ in batch.values()],
            metadatas=[meta for _, meta in batch.values()],  # type: ignore[misc]  # None is allowed
        )
        added = self._collection.count() - before
        logger.info(
            "Ingested %d documents, %d new (total now: %d)", len(batch), added, before + added
        )
        return added

    @staticmethod
    def _doc_id(text: str, metadata: dict | None) -> str:
        payload = json.dumps([text, metadata or {}], sort_keys=True, default=str)
        return "doc_" + hashlib.sha256(payload.encode()).hexdigest()[:20]

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        filter: dict | None = None,
    ) -> list[dict]:
        """
        Retrieve the most relevant documents for a query.

        Returns a list of dicts with keys: ``text``, ``metadata``, ``distance``.
        """
        total = self._collection.count()  # one store round-trip, not three
        if total == 0:
            return []
        kwargs: dict = {"query_texts": [query_text], "n_results": min(n_results, total)}
        if filter:
            kwargs["where"] = filter

        try:
            results = self._collection.query(**kwargs)
        except Exception as exc:
            logger.error("ChromaDB query failed: %s", exc)
            return []

        docs: list[dict] = []
        for text, meta, dist in zip(
            (results["documents"] or [[]])[0],
            (results["metadatas"] or [[]])[0],
            (results["distances"] or [[]])[0],
        ):
            docs.append(
                {
                    "text": text,
                    "metadata": meta,
                    "distance": dist,
                }
            )
        return docs

    def list_documents(self) -> list[dict]:
        """Return all documents currently in the collection."""
        if self._collection.count() == 0:
            return []
        data = self._collection.get()
        docs = []
        for doc_id, text, meta in zip(
            data["ids"], data["documents"] or [], data["metadatas"] or []
        ):
            docs.append(
                {
                    "id": doc_id,
                    "text": text[:200] + "..." if len(text) > 200 else text,
                    "metadata": meta,
                }
            )
        return docs

    # ------------------------------------------------------------------
    # Initial document loading
    # ------------------------------------------------------------------

    def load_initial_docs(self) -> int:
        """
        Load sample documentation from the ``data/robot_docs/`` directory.
        Skips loading if documents already exist.

        Returns the number of documents added.
        """
        if self._collection.count() > 0:
            logger.info("Documents already loaded (%d), skipping.", self._collection.count())
            return 0

        if not _DOCS_DIR.is_dir():
            logger.warning("Docs directory %s not found, skipping initial load.", _DOCS_DIR)
            return 0

        documents: list[dict] = []
        category_map = {
            "robot_manual": "operations",
            "sensor_specifications": "specifications",
            "maintenance_guide": "maintenance",
            "troubleshooting": "troubleshooting",
            "safety_protocols": "safety",
        }

        for fpath in sorted(_DOCS_DIR.glob("*.txt")):
            text = fpath.read_text(encoding="utf-8").strip()
            if not text:
                continue

            stem = fpath.stem
            category = category_map.get(stem, "general")

            # Split long docs into chunks (~500 chars) for better retrieval
            chunks = self._chunk_text(text, max_chars=500)
            for idx, chunk in enumerate(chunks):
                documents.append(
                    {
                        "text": chunk,
                        "metadata": {
                            "source": fpath.name,
                            "category": category,
                            "chunk_index": idx,
                        },
                    }
                )

        added = self.add_documents(documents)
        logger.info("Loaded %d document chunks from %s", added, _DOCS_DIR)
        return added

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 500) -> list[str]:
        """Split text into chunks on paragraph boundaries."""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[str] = []
        current = ""
        for para in paragraphs:
            if current and len(current) + len(para) + 2 > max_chars:
                chunks.append(current)
                current = para
            else:
                current = f"{current}\n\n{para}".strip() if current else para
        if current:
            chunks.append(current)
        return chunks if chunks else [text]
