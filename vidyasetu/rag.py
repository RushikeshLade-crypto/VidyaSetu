"""Step 2 - Retrieve (RAG): index the student's own textbook so doubts are answered
from it and nothing else. Uses ChromaDB when installed, otherwise SQLite + numpy."""
import json

import numpy as np

from . import config, db, ingest, llm


def make_chunks(pages: list[dict]) -> list[dict]:
    """Each teachable paragraph is one chunk and remembers its page number."""
    return [{"page": p["page"], "text": p["text"]} for p in ingest.split_paragraphs(pages, min_len=40)]


class SqliteStore:
    name = "sqlite"

    def add(self, lid: str, chunks: list[dict], embs: list[list[float]]) -> None:
        with db.conn() as c:
            c.execute("DELETE FROM chunks WHERE lesson_id=?", (lid,))
            c.executemany(
                "INSERT INTO chunks VALUES (?,?,?,?,?)",
                [(lid, i, ch["page"], ch["text"], json.dumps(e)) for i, (ch, e) in enumerate(zip(chunks, embs))],
            )

    def search(self, lid: str, emb: list[float], k: int) -> list[dict]:
        with db.conn() as c:
            rows = c.execute("SELECT page,text,emb FROM chunks WHERE lesson_id=?", (lid,)).fetchall()
        if not rows:
            return []
        mat = np.array([json.loads(r["emb"]) for r in rows], dtype=np.float32)
        q = np.array(emb, dtype=np.float32)
        sims = mat @ q / (np.linalg.norm(mat, axis=1) * np.linalg.norm(q) + 1e-9)
        order = np.argsort(-sims)[:k]
        return [{"page": rows[i]["page"], "text": rows[i]["text"], "score": float(sims[i])} for i in order]


    def delete(self, lid: str) -> None:
        pass  # rows are removed by db.delete_lesson


class ChromaStore:
    name = "chroma"

    def __init__(self):
        import chromadb

        self.client = chromadb.PersistentClient(path=str(config.DATA_DIR / "chroma"))

    def _col(self, lid: str):
        return self.client.get_or_create_collection(name=f"l_{lid}", metadata={"hnsw:space": "cosine"})

    def add(self, lid: str, chunks: list[dict], embs: list[list[float]]) -> None:
        try:
            self.client.delete_collection(f"l_{lid}")
        except Exception:
            pass
        self._col(lid).add(
            ids=[str(i) for i in range(len(chunks))],
            embeddings=embs,
            documents=[c["text"] for c in chunks],
            metadatas=[{"page": c["page"]} for c in chunks],
        )

    def delete(self, lid: str) -> None:
        try:
            self.client.delete_collection(f"l_{lid}")
        except Exception:
            pass

    def search(self, lid: str, emb: list[float], k: int) -> list[dict]:
        col = self._col(lid)
        n = col.count()
        if not n:
            return []
        res = col.query(query_embeddings=[emb], n_results=min(k, n))
        return [
            {"page": m["page"], "text": d, "score": 1.0 - float(dist)}
            for d, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
        ]


_store = None


def store():
    global _store
    if _store is None:
        if config.VECTOR_BACKEND in ("auto", "chroma"):
            try:
                _store = ChromaStore()
            except Exception:
                if config.VECTOR_BACKEND == "chroma":
                    raise
        if _store is None:
            _store = SqliteStore()
    return _store


def index(lid: str, pages: list[dict]) -> int:
    chunks = make_chunks(pages)
    store().add(lid, chunks, llm.embed([c["text"] for c in chunks]))
    return len(chunks)


def search(lid: str, question_en: str, k: int = 4) -> list[dict]:
    return store().search(lid, llm.embed([question_en])[0], k)
