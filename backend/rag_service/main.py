"""
NeuraFleet RAG Service
======================

FastAPI microservice providing Retrieval-Augmented Generation (RAG)
over robot fleet documentation.  Uses ChromaDB for vector storage
and Anthropic Claude (or a rule-based fallback) for response generation.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi.concurrency import run_in_threadpool
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from llm_client import LLMClient
from vector_store import VectorStore

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rag_service")

# ---------------------------------------------------------------------------
# Global instances
# ---------------------------------------------------------------------------

vector_store: VectorStore | None = None
llm_client: LLMClient | None = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global vector_store, llm_client
    logger.info("RAG Service starting up")

    vector_store = VectorStore()
    loaded = vector_store.load_initial_docs()
    logger.info("Initial docs loaded: %d chunks", loaded)

    llm_client = LLMClient()
    yield
    logger.info("RAG Service shut down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NeuraFleet RAG Service",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    doc_count = await run_in_threadpool(vector_store._collection.count) if vector_store else 0
    return {
        "status": "ok",
        "documents": doc_count,
        "llm_available": llm_client is not None and llm_client._client is not None,
    }


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    robot_id: Optional[str] = None


class QueryResponse(BaseModel):
    response: str
    sources: List[str]
    query: str


class IngestRequest(BaseModel):
    documents: List[dict]


class IngestResponse(BaseModel):
    added: int


# ---------------------------------------------------------------------------
# POST /query
# ---------------------------------------------------------------------------

@app.post("/query", response_model=QueryResponse)
async def query_rag(req: QueryRequest):
    """
    Query the RAG system.

    Retrieves the most relevant documentation chunks, then generates
    a contextual response via the LLM (or fallback engine).
    """
    if vector_store is None or llm_client is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Build optional filter
    vs_filter = None
    if req.robot_id:
        # Optionally could filter by robot_type metadata; for now just pass it to the LLM
        pass

    # Retrieve relevant chunks
    results = await run_in_threadpool(
        vector_store.query,
        query_text=req.query,
        n_results=5,
        filter=vs_filter,
    )

    context_texts = [r["text"] for r in results]
    sources = list({r["metadata"].get("source", "unknown") for r in results})

    # Generate response
    response_text = await llm_client.generate_response(
        query=req.query,
        context=context_texts,
        robot_id=req.robot_id,
    )

    return QueryResponse(
        response=response_text,
        sources=sources,
        query=req.query,
    )


# ---------------------------------------------------------------------------
# GET /documents
# ---------------------------------------------------------------------------

@app.get("/documents")
async def list_documents():
    """List all documents in the vector store."""
    if vector_store is None:
        return []
    return await run_in_threadpool(vector_store.list_documents)


# ---------------------------------------------------------------------------
# POST /ingest
# ---------------------------------------------------------------------------

@app.post("/ingest", response_model=IngestResponse)
async def ingest_documents(req: IngestRequest):
    """
    Add new documents to the vector store.

    Each document should have at least a ``text`` field.
    Optional ``metadata`` dict with ``source``, ``robot_type``, ``category``.
    """
    if vector_store is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    added = await run_in_threadpool(vector_store.add_documents, req.documents)
    return IngestResponse(added=added)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8003,
        reload=False,
        log_level="info",
    )
