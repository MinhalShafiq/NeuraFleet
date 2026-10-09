"""VectorStore must really persist (plan 0.3) and read the env var the deploy files set (plan 0.4)."""

import re

import pytest
from conftest import ROOT, load_module

pytest.importorskip("chromadb")


class _HashEmbedding:
    """Deterministic offline embedding so tests don't download a model."""

    def __init__(self):
        pass

    def name(self):
        return "test-hash-embedding"

    def __call__(self, input):
        out = []
        for text in input:
            vec = [0.0] * 32
            for word in text.lower().split():
                vec[hash_word(word) % 32] += 1.0
            out.append(vec)
        return out


def hash_word(w):
    return sum(ord(c) * (i + 1) for i, c in enumerate(w))


@pytest.fixture
def VectorStore():
    return load_module("rag-service", "vector_store").VectorStore


def test_round_trip(VectorStore, tmp_path):
    vs = VectorStore(persist_dir=str(tmp_path), embedding_function=_HashEmbedding())
    assert (
        vs.add_documents(
            [
                {"text": "battery charging procedure", "metadata": {"source": "a"}},
                {"text": "lidar calibration steps", "metadata": {"source": "b"}},
            ]
        )
        == 2
    )
    hits = vs.query("battery charging", n_results=1)
    assert hits and hits[0]["metadata"]["source"] == "a"


def test_data_survives_restart(VectorStore, tmp_path):
    VectorStore(persist_dir=str(tmp_path), embedding_function=_HashEmbedding()).add_documents(
        [{"text": "persisted doc", "metadata": {}}]
    )
    reopened = VectorStore(persist_dir=str(tmp_path), embedding_function=_HashEmbedding())
    assert reopened._collection.count() == 1


def test_env_var_name_matches_deploy_config():
    code = (ROOT / "backend/rag_service/vector_store.py").read_text()
    var = re.search(r'getenv\("(CHROMA_\w+)"', code).group(1)
    for f in ("docker-compose.yml", "k8s/configmap.yaml"):
        assert var in (ROOT / f).read_text(), f"{f} does not set {var}"


def test_rag_requirements_pin_numpy_below_2():
    """chromadb 0.4.x imports np.float_, removed in NumPy 2 -> RAG container crash-loops."""
    reqs = (ROOT / "backend/rag_service/requirements.txt").read_text()
    m = re.search(r"^numpy==(\d+)\.", reqs, re.M)
    assert m and int(m.group(1)) < 2


# ---------------------------------------------------------------- IDs (plan 2.6)
def _docs(*texts):
    return [{"text": t, "metadata": {"source": f"{t}.txt"}} for t in texts]


def test_reingesting_the_same_documents_is_idempotent(VectorStore, tmp_path):
    vs = VectorStore(persist_dir=str(tmp_path), embedding_function=_HashEmbedding())
    assert vs.add_documents(_docs("alpha", "beta")) == 2
    assert vs.add_documents(_docs("alpha", "beta")) == 0, "second ingest must add nothing"
    assert vs._collection.count() == 2


def test_new_documents_never_overwrite_existing_ones_after_a_delete(VectorStore, tmp_path):
    """The old ``doc_{count + i}`` IDs: ingest [A, B] -> doc_0, doc_1; delete A -> count 1;
    ingest [C] -> doc_1, silently overwriting B."""
    vs = VectorStore(persist_dir=str(tmp_path), embedding_function=_HashEmbedding())
    vs.add_documents(_docs("alpha", "beta"))
    alpha_id = next(d["id"] for d in vs.list_documents() if d["text"] == "alpha")
    vs._collection.delete(ids=[alpha_id])
    vs.add_documents(_docs("gamma"))
    texts = sorted(d["text"] for d in vs.list_documents())
    assert texts == ["beta", "gamma"], texts


def test_duplicates_within_one_batch_are_collapsed(VectorStore, tmp_path):
    vs = VectorStore(persist_dir=str(tmp_path), embedding_function=_HashEmbedding())
    assert vs.add_documents(_docs("same", "same", "other")) == 2


def test_same_text_with_different_metadata_are_distinct_documents(VectorStore, tmp_path):
    vs = VectorStore(persist_dir=str(tmp_path), embedding_function=_HashEmbedding())
    vs.add_documents(
        [{"text": "t", "metadata": {"source": "a"}}, {"text": "t", "metadata": {"source": "b"}}]
    )
    assert vs._collection.count() == 2


def test_query_makes_a_single_count_call(VectorStore, tmp_path):
    vs = VectorStore(persist_dir=str(tmp_path), embedding_function=_HashEmbedding())
    vs.add_documents(_docs("alpha", "beta"))
    calls = []

    class Spy:
        """Delegates to the real collection, recording count() round-trips."""

        def __init__(self, real):
            self._real = real

        def count(self):
            calls.append(1)
            return self._real.count()

        def __getattr__(self, name):
            return getattr(self._real, name)

    vs._collection = Spy(vs._collection)
    vs.query("alpha")
    assert len(calls) == 1


def test_ingest_without_metadata_then_query_does_not_crash(VectorStore, tmp_path):
    import asyncio

    import httpx

    rag = load_module("rag-service", "main")
    llm = load_module("rag-service", "llm_client")
    store = VectorStore(persist_dir=str(tmp_path), embedding_function=_HashEmbedding())
    old = (rag.vector_store, rag.llm_client)
    rag.vector_store, rag.llm_client = store, llm.LLMClient()

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=rag.app), base_url="http://t"
        ) as c:
            added = await c.post(
                "/ingest", json={"documents": [{"text": "battery charging steps"}]}
            )
            again = await c.post(
                "/ingest", json={"documents": [{"text": "battery charging steps"}]}
            )
            q = await c.post("/query", json={"query": "battery charging"})
            bad = await c.post("/ingest", json={"documents": []})
            return added, again, q, bad

    try:
        added, again, q, bad = asyncio.run(run())
    finally:
        rag.vector_store, rag.llm_client = old
    assert added.json() == {"added": 1} and again.json() == {"added": 0}
    assert q.status_code == 200 and q.json()["sources"] == ["unknown"]
    assert bad.status_code == 422
