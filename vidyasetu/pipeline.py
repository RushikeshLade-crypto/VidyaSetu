"""The VidyaSetu pipeline: Ingest -> Retrieve (RAG) -> Generate -> Translate -> Verify & Speak.

Speech output/input runs in the browser (Web Speech API), so the server only
produces text."""
import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import config, db, glossary, ingest, languages, llm, rag

JOBS: dict[str, dict] = {}

# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------
SIMPLIFY_SYS = (
    "You are a patient school teacher. Rewrite the passage for a student in grade {grade}. "
    "Use short, clear sentences and everyday words. Keep every fact, number, unit, formula and "
    "technical term exactly as in the passage and do not add new facts. Do not make it longer. "
    "Output only the rewritten passage as plain text, with no preface."
)

TRANSLATE_SYS = (
    "You are an expert translator of school textbooks into {lang}. Translate the English text for a "
    "student in grade {grade}, using simple, natural {lang} that a student would speak. Rules: "
    "(1) Tokens like [[T1]] are locked technical terms; copy each one exactly as written, never "
    "translate or remove it. (2) Keep numbers, units, symbols and formulas unchanged. (3) Do not add "
    "or omit information. Output only the translation."
)
TRANSLATE_STRICT = (
    " IMPORTANT: your last answer dropped some [[T#]] tokens. Every [[T#]] token from the input MUST "
    "appear in your output exactly as written."
)
TRANSLATE_MANY_SYS = (
    "You are an expert translator of school textbooks into {lang} for a student in grade {grade}. "
    "The input has numbered items like <<1>> text. Translate every item into simple, natural {lang}. "
    "Keep the <<n>> markers and the order. Copy tokens like [[T1]] exactly. Keep numbers, units and "
    "blanks (______) unchanged. Output only the numbered items."
)
BACK_SYS = (
    "Translate the following {lang} text into English as literally and faithfully as possible. "
    "Do not improve or correct it. Output only the English translation."
)
TOPIC_SYS = (
    "You are a school teacher. Write a short textbook-style lesson of 4 to 5 short paragraphs for a "
    "student in grade {grade} on the given topic. Plain text only: no headings, bullets or markdown. "
    "Be accurate and include only well-established facts."
)
ANSWER_SYS = (
    "You are a study helper for a student in grade {grade}. Answer the question using ONLY the "
    "textbook excerpts provided. If the answer is not in the excerpts, reply with exactly "
    "NOT_IN_TEXTBOOK. Explain simply in at most 4 short sentences and mention the page like (p. 2). "
    "Keep technical terms as they appear in the excerpts."
)
Q_TO_EN_SYS = (
    "Translate the student's question from {lang} into clear English. Output only the English question."
)
QUIZ_SYS = (
    "You write quizzes for school students. From the lesson text, write {n} multiple-choice questions "
    "for grade {grade}. Use only facts stated in the text. Each question has exactly 4 options and one "
    "correct answer. Return ONLY valid JSON, no other text, in this shape: "
    '{{"questions":[{{"question":"...","options":["...","...","...","..."],'
    '"answer_index":0,"explanation":"one short sentence"}}]}}'
)
NOT_FOUND_EN = "I could not find the answer to this in your textbook. Try asking in a different way."


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _clean(out: str) -> str:
    out = out.strip()
    out = re.sub(r"^```[a-z]*\n?|\n?```$", "", out).strip()
    out = re.sub(r"^(translation|translated text|rewritten passage)\s*:\s*", "", out, flags=re.I)
    return out.strip().strip('"“”').strip()


def _tokens_budget(text: str, factor: int = 3) -> int:
    return int(min(4096, max(512, len(text) * factor)))


def cosine(a, b) -> float:
    a, b = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def parse_json(raw: str) -> dict:
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("model did not return JSON")
    return json.loads(raw[start : end + 1])


# --------------------------------------------------------------------------
# Step 3 - Generate
# --------------------------------------------------------------------------
def simplify(text: str, grade: int) -> str:
    out = _clean(
        llm.chat("simplify", SIMPLIFY_SYS.format(grade=grade), text, max_tokens=_tokens_budget(text, 2),
                 temperature=0.2, payload={"text": text})
    )
    return out or text


def topic_text(topic: str, grade: int) -> str:
    return _clean(
        llm.chat("topic_lesson", TOPIC_SYS.format(grade=grade), f"Topic: {topic}", max_tokens=900,
                 temperature=0.4, payload={"topic": topic})
    )


# --------------------------------------------------------------------------
# Step 4 - Translate (with glossary lock)
# --------------------------------------------------------------------------
def translate_text(text: str, lang: str, grade: int) -> dict:
    locked, mapping = glossary.lock(text, lang)
    system = TRANSLATE_SYS.format(lang=languages.name(lang), grade=grade)
    payload = {"text": text, "locked": locked, "mapping": mapping, "lang": lang}

    def run(sys_prompt: str) -> str:
        out = llm.chat("translate", sys_prompt, locked, model=config.TRANSLATE_MODEL,
                       max_tokens=_tokens_budget(text), temperature=0.1, payload=payload)
        return glossary.normalise_tokens(_clean(out))

    out = run(system)
    missing = [t for t in mapping if t not in out]
    if missing:
        out = run(system + TRANSLATE_STRICT)
        missing = [t for t in mapping if t not in out]
    return {
        "text": glossary.unlock(out, mapping, lang),
        "terms": glossary.used_terms({t: mapping[t] for t in mapping if t not in missing}, lang),
        "missing": len(missing),
    }


def translate_many(texts: list[str], lang: str, grade: int) -> list[str]:
    items = []
    for t in texts:
        locked, mapping = glossary.lock(t, lang)
        items.append({"text": t, "locked": locked, "mapping": mapping})
    numbered = "\n".join(f"<<{i}>> {it['locked']}" for i, it in enumerate(items, 1))
    out = llm.chat(
        "translate_many",
        TRANSLATE_MANY_SYS.format(lang=languages.name(lang), grade=grade),
        numbered,
        model=config.TRANSLATE_MODEL,
        max_tokens=_tokens_budget(numbered),
        temperature=0.1,
        payload={"items": items, "lang": lang},
    )
    parts = re.split(r"<<\s*(\d+)\s*>>", glossary.normalise_tokens(out))
    found = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    result = []
    for i, it in enumerate(items, 1):
        piece = found.get(i)
        if not piece or any(tok not in piece for tok in it["mapping"]):
            result.append(translate_text(it["text"], lang, grade)["text"])  # fall back to one-by-one
        else:
            result.append(glossary.unlock(piece, it["mapping"], lang))
    return result


# --------------------------------------------------------------------------
# Step 5 - Verify
# --------------------------------------------------------------------------
def verify(simple_en: str, translated: str, lang: str, missing_terms: int) -> dict:
    if llm.provider_for("back_translate") == "mock":
        return {"status": "demo", "score": None, "missing_terms": missing_terms}
    back = _clean(
        llm.chat("back_translate", BACK_SYS.format(lang=languages.name(lang)), translated,
                 model=config.TRANSLATE_MODEL, max_tokens=_tokens_budget(simple_en, 2), temperature=0.0,
                 payload={"reference": simple_en})
    )
    a, b = llm.embed([simple_en, back])
    score = round(cosine(a, b), 3)
    ok = score >= config.QUALITY_THRESHOLD and missing_terms == 0
    return {"status": "ok" if ok else "review", "score": score, "missing_terms": missing_terms, "back": back}


def process_paragraph(p: dict, lang: str, grade: int, do_simplify: bool = True, do_verify: bool = True) -> dict:
    base = {"page": p["page"], "original": p["text"]}
    try:
        simple = simplify(p["text"], grade) if do_simplify else p["text"]
        tr = translate_text(simple, lang, grade)
        quality = verify(simple, tr["text"], lang, tr["missing"]) if do_verify else {"status": "unchecked"}
        return {**base, "simple": simple, "translated": tr["text"], "terms": tr["terms"], "quality": quality}
    except Exception as exc:  # keep the lesson usable if one paragraph fails
        return {**base, "simple": p["text"], "translated": "", "terms": [], "quality": {"status": "error"},
                "error": str(exc)}


# --------------------------------------------------------------------------
# Lesson jobs (run in a background thread; the browser polls for progress)
# --------------------------------------------------------------------------
def start_lesson(*, title: str, lang: str, grade: int, pages: list[dict] | None = None,
                 topic: str | None = None, first: int | None = None, last: int | None = None) -> str:
    lid = uuid.uuid4().hex[:10]
    JOBS[lid] = {"id": lid, "title": title, "lang": lang, "grade": grade, "status": "running",
                 "stage": "Starting", "done": 0, "total": 0, "paragraphs": [], "terms": [], "error": None,
                 "mock": llm.is_mock()}
    threading.Thread(target=_run, args=(lid, pages, topic, first, last), daemon=True).start()
    return lid


def _run(lid: str, pages, topic, first, last) -> None:
    job = JOBS[lid]
    lang, grade = job["lang"], job["grade"]
    try:
        job["stage"] = "Reading your material"
        if topic:
            pages = ingest.text_pages(topic_text(topic, grade))
        pages = ingest.select_pages(pages, first, last)
        paragraphs = ingest.split_paragraphs(pages)[: config.MAX_PARAGRAPHS]
        if not paragraphs:
            raise ValueError("No readable text found.")
        job["total"] = len(paragraphs)

        job["stage"] = "Indexing the textbook so you can ask doubts"
        rag.index(lid, pages)

        job["stage"] = "Simplifying and translating"
        seen: set[str] = set()
        with ThreadPoolExecutor(max_workers=config.WORKERS) as ex:
            for res in ex.map(lambda p: process_paragraph(p, lang, grade), paragraphs):
                job["paragraphs"].append(res)
                job["done"] += 1
                for t in res["terms"]:
                    if t["term"] not in seen:
                        seen.add(t["term"])
                        job["terms"].append(t)
        if JOBS.get(lid) is not job:  # deleted while it was being built
            rag.store().delete(lid)
            return
        failed = [p for p in job["paragraphs"] if p.get("error")]
        if len(failed) == len(job["paragraphs"]):
            raise RuntimeError(failed[0]["error"])
        result = {k: job[k] for k in ("id", "title", "lang", "grade", "paragraphs", "terms", "mock")}
        db.save_lesson(lid, job["title"], lang, grade, result)
        job["status"] = "done"
        job["stage"] = "Done"
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)


def lesson_state(lid: str) -> dict | None:
    job = JOBS.get(lid)
    if job and job["status"] != "done":
        return {**job, "paragraphs": list(job["paragraphs"]), "terms": list(job["terms"])}
    row = db.get_lesson(lid)
    if not row:
        return None
    return {**row["result"], "status": "done", "stage": "Done", "done": len(row["result"]["paragraphs"]),
            "total": len(row["result"]["paragraphs"]), "error": None}


# --------------------------------------------------------------------------
# Ask a doubt (voice loop: local language -> English -> RAG answer -> local language)
# --------------------------------------------------------------------------
def ask(lid: str, question: str) -> dict:
    lesson = lesson_state(lid)
    if not lesson:
        raise LookupError("lesson not found")
    lang, grade = lesson["lang"], lesson["grade"]
    question = question.strip()[:500]
    if re.search(r"[^\x00-\x7F]", question):
        q_en = _clean(llm.chat("q_to_en", Q_TO_EN_SYS.format(lang=languages.name(lang)), question,
                               max_tokens=120, temperature=0.0, payload={"question": question}))
    else:
        q_en = question
    hits = rag.search(lid, q_en, k=4)
    context = "\n\n".join(f"[Page {h['page']}] {h['text']}" for h in hits)
    answer_en = _clean(
        llm.chat("answer", ANSWER_SYS.format(grade=grade), f"Textbook excerpts:\n{context}\n\nQuestion: {q_en}",
                 max_tokens=400, temperature=0.1, payload={"hits": hits, "question": q_en})
    )
    found = "NOT_IN_TEXTBOOK" not in answer_en
    if not found:
        answer_en = NOT_FOUND_EN
    answer = translate_text(answer_en, lang, grade)["text"]
    sources = [{"page": h["page"], "snippet": h["text"][:220]} for h in hits[:2]] if found else []
    return {"question_en": q_en, "answer_en": answer_en, "answer": answer, "found": found, "sources": sources}


# --------------------------------------------------------------------------
# Quiz
# --------------------------------------------------------------------------
def make_quiz(lid: str, n: int = 5) -> list[dict]:
    lesson = lesson_state(lid)
    if not lesson:
        raise LookupError("lesson not found")
    lang, grade = lesson["lang"], lesson["grade"]
    text = "\n\n".join(p["simple"] for p in lesson["paragraphs"])[:6000]
    data = None
    for attempt in range(2):
        raw = llm.chat("quiz", QUIZ_SYS.format(n=n, grade=grade), text, max_tokens=1800, temperature=0.3,
                       payload={"text": text, "n": n, "lang": lang}, json_mode=True)
        try:
            data = parse_json(raw)
            break
        except ValueError:
            continue
    if data is None:
        raise RuntimeError("The model did not return a valid quiz. Please try again.")
    qs = []
    for q in data.get("questions", []):
        opts = q.get("options") or []
        idx = q.get("answer_index")
        if isinstance(idx, int) and 2 <= len(opts) <= 6 and 0 <= idx < len(opts) and q.get("question"):
            qs.append({"question": q["question"], "options": [str(o) for o in opts], "answer_index": idx,
                       "explanation": q.get("explanation", "")})
    qs = qs[:n]
    if not qs:
        raise RuntimeError("Could not build quiz questions from this lesson.")
    flat = []
    for q in qs:
        flat += [q["question"], *q["options"], q["explanation"] or q["question"]]
    local = translate_many(flat, lang, grade)
    out, pos = [], 0
    for q in qs:
        k = len(q["options"])
        out.append({
            "question": {"en": q["question"], "local": local[pos]},
            "options": [{"en": o, "local": local[pos + 1 + i]} for i, o in enumerate(q["options"])],
            "answer_index": q["answer_index"],
            "explanation": {"en": q["explanation"], "local": local[pos + 1 + k]},
        })
        pos += k + 2
    return out


# --------------------------------------------------------------------------
# Teacher mode: one worksheet -> many languages
# --------------------------------------------------------------------------
def teacher_convert(text: str, langs: list[str], grade: int, do_simplify: bool) -> dict:
    paragraphs = ingest.split_paragraphs(ingest.text_pages(text), min_len=0)[: config.MAX_PARAGRAPHS]
    jobs = [(lang, i, p) for lang in langs for i, p in enumerate(paragraphs)]
    with ThreadPoolExecutor(max_workers=max(config.WORKERS, 4)) as ex:
        results = list(ex.map(lambda j: process_paragraph(j[2], j[0], grade, do_simplify, True), jobs))
    out: dict[str, dict] = {lang: {"paragraphs": []} for lang in langs}
    for (lang, _i, _p), res in zip(jobs, results):
        out[lang]["paragraphs"].append(res)
    for lang, block in out.items():
        block["text"] = "\n\n".join(p["translated"] for p in block["paragraphs"] if p["translated"])
        block["review"] = sum(1 for p in block["paragraphs"] if p["quality"].get("status") in ("review", "error"))
    return out
