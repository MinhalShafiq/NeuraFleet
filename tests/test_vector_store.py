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
    assert vs.add_documents([
        {"text": "battery charging procedure", "metadata": {"source": "a"}},
        {"text": "lidar calibration steps", "metadata": {"source": "b"}},
    ]) == 2
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
