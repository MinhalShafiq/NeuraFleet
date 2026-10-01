"""
NeuraFleet RAG Vector Store
============================

Manages a ChromaDB collection of robot fleet documentation.
Uses sentence-transformers (all-MiniLM-L6-v2) for embeddings
and cosine similarity for retrieval.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

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

    Embeddings are computed by ChromaDB's built-in SentenceTransformer
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

    def add_documents(self, documents: List[Dict]) -> int:
        """
        Add documents to the collection.

        Each dict should have:
          - ``text`` (str): the document content
          - ``metadata`` (dict, optional): e.g. source, robot_type, category

        Returns the number of documents added.
        """
        if not documents:
            return 0

        ids: List[str] = []
        texts: List[str] = []
        metadatas: List[Optional[dict]] = []

        base = self._collection.count()
        for i, doc in enumerate(documents):
            doc_id = f"doc_{base + i}"
            ids.append(doc_id)
            texts.append(doc["text"])
            metadatas.append(doc.get("metadata") or None)  # Chroma rejects {}

        self._collection.add(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
        )
        logger.info("Added %d documents (total now: %d)", len(ids), self._collection.count())
        return len(ids)

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        filter: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Retrieve the most relevant documents for a query.

        Returns a list of dicts with keys: ``text``, ``metadata``, ``distance``.
        """
        kwargs: dict = {
            "query_texts": [query_text],
            "n_results": min(n_results, max(1, self._collection.count())),
        }
        if filter:
            kwargs["where"] = filter

        if self._collection.count() == 0:
            return []

        try:
            results = self._collection.query(**kwargs)
        except Exception as exc:
            logger.error("ChromaDB query failed: %s", exc)
            return []

        docs: List[Dict] = []
        for text, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            docs.append({
                "text": text,
                "metadata": meta,
                "distance": dist,
            })
        return docs

    def list_documents(self) -> List[Dict]:
        """Return all documents currently in the collection."""
        if self._collection.count() == 0:
            return []
        data = self._collection.get()
        docs = []
        for doc_id, text, meta in zip(data["ids"], data["documents"], data["metadatas"]):
            docs.append({"id": doc_id, "text": text[:200] + "..." if len(text) > 200 else text, "metadata": meta})
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

        documents: List[Dict] = []
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
                documents.append({
                    "text": chunk,
                    "metadata": {
                        "source": fpath.name,
                        "category": category,
                        "chunk_index": idx,
                    },
                })

        added = self.add_documents(documents)
        logger.info("Loaded %d document chunks from %s", added, _DOCS_DIR)
        return added

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 500) -> List[str]:
        """Split text into chunks on paragraph boundaries."""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: List[str] = []
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
