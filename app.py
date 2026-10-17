"""VidyaSetu web server.  Run:  python app.py   then open http://localhost:5000"""
import hashlib
import os
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from vidyasetu import config, db, ingest, languages, llm, mock, pipeline, rag, sarvam, watsonx

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024
db.init()


def fail(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _int(value, default=None, lo=None, hi=None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


@app.errorhandler(413)
def too_big(_e):
    return fail(f"File is larger than {config.MAX_UPLOAD_MB} MB.", 413)


@app.errorhandler(Exception)
def unexpected(exc):
    from werkzeug.exceptions import HTTPException

    if isinstance(exc, HTTPException):
        return fail(exc.description, exc.code)
    if isinstance(exc, (sarvam.SarvamError, watsonx.WatsonxError)):
        app.logger.warning("provider error: %s", exc)
        return fail(str(exc), 502)
    app.logger.exception("unhandled error")
    return fail(str(exc) or "Something went wrong.", 500)


@app.get("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


PROVIDER_LABEL = {"watsonx": "IBM Granite", "sarvam": "Sarvam", "mock": "placeholder"}


def run_summary() -> dict:
    gen, tr = config.GEN_PROVIDER, config.TRANSLATE_PROVIDER
    real = [PROVIDER_LABEL[p] for p in dict.fromkeys([gen, tr]) if p != "mock"]
    if llm.is_mock():
        mode, label = "demo", "Demo mode"
    else:
        mode = "live" if "mock" not in (gen, tr) else "partial"
        label = "Live · " + " + ".join(real) + ("" if mode == "live" else " (some steps demo)")
    return {"mode": mode, "label": label}


@app.get("/api/config")
def get_config():
    return jsonify({
        **run_summary(),
        "languages": [{"code": c, **v, "neural_tts": c in languages.NEURAL_TTS} for c, v in languages.LANGUAGES.items()],
        "providers": {"generate": config.GEN_PROVIDER, "translate": config.TRANSLATE_PROVIDER,
                      "embed": config.EMBED_PROVIDER, "speech": config.SPEECH_PROVIDER},
        "vector_store": rag.store().name,
    })


AUDIO_DIR = config.DATA_DIR / "audio"


@app.post("/api/tts")
def tts():
    """Studio-quality voice (Sarvam Bulbul). Audio is cached so repeats cost nothing."""
    body = request.get_json(silent=True) or {}
    lang, text = body.get("lang"), (body.get("text") or "").strip()
    if config.SPEECH_PROVIDER != "sarvam":
        return fail("Studio voice is not enabled.", 404)
    if lang not in languages.NEURAL_TTS:
        return fail("No studio voice for this language; the browser voice will be used.", 400)
    text = re.sub(r"\s*\([A-Za-z][A-Za-z\s\-]*\)", "", text)[:2400].strip()  # don't read the English helper terms
    if not text:
        return fail("Nothing to read.")
    key = hashlib.sha1(f"{config.SARVAM_TTS_MODEL}|{config.SARVAM_TTS_SPEAKER}|{lang}|{text}".encode()).hexdigest()
    path = AUDIO_DIR / f"{key}.wav"
    if not path.exists():
        wav = sarvam.tts(text, languages.LANGUAGES[lang]["speech"])
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(wav)
        tmp.replace(path)
    return send_file(path, mimetype="audio/wav", max_age=86400)


@app.post("/api/lessons")
def create_lesson():
    form = request.form
    lang = form.get("lang", "hi")
    if not languages.valid(lang):
        return fail("Choose a language from the list.")
    grade = _int(form.get("grade"), 6, 1, 12)
    first, last = _int(form.get("page_from")), _int(form.get("page_to"))
    title = (form.get("title") or "").strip()[:80]
    try:
        upload = request.files.get("file")
        text = (form.get("text") or "").strip()
        topic = (form.get("topic") or "").strip()[:200]
        if upload and upload.filename:
            pages = ingest.read_upload(upload.filename, upload.read())
            title = title or Path(upload.filename).stem[:60]
            lid = pipeline.start_lesson(title=title, lang=lang, grade=grade, pages=pages, first=first, last=last)
        elif text:
            title = title or text[:40].strip() + "…"
            lid = pipeline.start_lesson(title=title, lang=lang, grade=grade, pages=ingest.text_pages(text[:60000]))
        elif topic:
            lid = pipeline.start_lesson(title=title or topic, lang=lang, grade=grade, topic=topic)
        else:
            return fail("Upload a file, paste some text, or type a topic.")
    except ingest.IngestError as exc:
        return fail(str(exc))
    return jsonify({"id": lid}), 202


@app.post("/api/lessons/sample")
def sample_lesson():
    lang = (request.get_json(silent=True) or {}).get("lang", "hi")
    if not languages.valid(lang):
        return fail("Choose a language from the list.")
    text = mock.SAMPLE_FILE.read_text(encoding="utf-8")
    lid = pipeline.start_lesson(title="Photosynthesis (sample chapter)", lang=lang, grade=6,
                                pages=ingest.text_pages(text))
    return jsonify({"id": lid}), 202


@app.get("/api/lessons")
def lessons():
    return jsonify(db.list_lessons())


@app.get("/api/lessons/<lid>")
def lesson(lid):
    state = pipeline.lesson_state(lid)
    if not state:
        return fail("Lesson not found.", 404)
    return jsonify(state)


@app.delete("/api/lessons/<lid>")
def delete_lesson(lid):
    if not re.fullmatch(r"[0-9a-f]{10}", lid):
        return fail("Lesson not found.", 404)
    pipeline.JOBS.pop(lid, None)
    found = db.delete_lesson(lid)
    rag.store().delete(lid)
    return jsonify({"ok": True}) if found else fail("Lesson not found.", 404)


@app.post("/api/lessons/<lid>/ask")
def ask(lid):
    q = ((request.get_json(silent=True) or {}).get("question") or "").strip()
    if not q:
        return fail("Type or speak a question first.")
    try:
        return jsonify(pipeline.ask(lid, q))
    except LookupError:
        return fail("Lesson not found.", 404)


@app.post("/api/lessons/<lid>/quiz")
def quiz(lid):
    n = _int((request.get_json(silent=True) or {}).get("n"), 5, 1, 10)
    try:
        return jsonify({"questions": pipeline.make_quiz(lid, n)})
    except LookupError:
        return fail("Lesson not found.", 404)


@app.route("/api/lessons/<lid>/results", methods=["GET", "POST"])
def results(lid):
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        score, total = _int(body.get("score"), 0, 0, 100), _int(body.get("total"), 0, 0, 100)
        if not total or score > total:
            return fail("Invalid score.")
        db.add_result(lid, score, total)
        return jsonify({"ok": True})
    return jsonify(db.get_results(lid))


@app.post("/api/teacher")
def teacher():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    langs = [c for c in body.get("langs", []) if languages.valid(c)][:6]
    if not text:
        return fail("Paste the worksheet or notes first.")
    if not langs:
        return fail("Pick at least one language.")
    grade = _int(body.get("grade"), 6, 1, 12)
    result = pipeline.teacher_convert(text[:4000], langs, grade, bool(body.get("simplify", False)))
    return jsonify(result)


@app.get("/healthz")
def health():
    return jsonify({"ok": True, "mode": run_summary()["mode"]})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "127.0.0.1")  # set HOST=0.0.0.0 to open it from a phone on the same Wi-Fi
    print(f"VidyaSetu: {run_summary()['label']}  |  generate={config.GEN_PROVIDER}, translate={config.TRANSLATE_PROVIDER}, "
          f"embed={config.EMBED_PROVIDER}, speech={config.SPEECH_PROVIDER}  ->  http://localhost:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
