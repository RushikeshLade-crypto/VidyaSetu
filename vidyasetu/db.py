"""SQLite storage for lessons, textbook chunks and quiz results."""
import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

DB_PATH = config.DATA_DIR / "vidyasetu.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY, title TEXT, lang TEXT, grade INTEGER,
    created REAL, result TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    lesson_id TEXT, idx INTEGER, page INTEGER, text TEXT, emb TEXT,
    PRIMARY KEY (lesson_id, idx)
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lesson_id TEXT,
    score INTEGER, total INTEGER, ts REAL
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def save_lesson(lid: str, title: str, lang: str, grade: int, result: dict) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO lessons VALUES (?,?,?,?,?,?)",
            (lid, title, lang, grade, time.time(), json.dumps(result, ensure_ascii=False)),
        )


def get_lesson(lid: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM lessons WHERE id=?", (lid,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["result"] = json.loads(out["result"])
    return out


def list_lessons() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT id,title,lang,grade,created FROM lessons ORDER BY created DESC LIMIT 50").fetchall()
    return [dict(r) for r in rows]


def add_result(lid: str, score: int, total: int) -> None:
    with conn() as c:
        c.execute("INSERT INTO results (lesson_id,score,total,ts) VALUES (?,?,?,?)", (lid, score, total, time.time()))


def get_results(lid: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT score,total,ts FROM results WHERE lesson_id=? ORDER BY ts DESC LIMIT 20", (lid,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_lesson(lid: str) -> bool:
    with conn() as c:
        found = c.execute("DELETE FROM lessons WHERE id=?", (lid,)).rowcount > 0
        c.execute("DELETE FROM chunks WHERE lesson_id=?", (lid,))
        c.execute("DELETE FROM results WHERE lesson_id=?", (lid,))
    return found
