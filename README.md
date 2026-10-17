# VidyaSetu

AI study companion that turns any English lesson into a lesson in the student's own language.
Built for Sociothon 26 (Theme: Education, Problem #3 *Education Beyond Language Barriers*).

Upload a chapter PDF, a photo of a textbook page, or just a topic. VidyaSetu **simplifies** it to the
student's class level, **translates** it with exam terms kept bilingual (`प्रकाशसंश्लेषण (photosynthesis)`),
**checks its own translation**, and lets the student **listen**, take **quizzes** and **ask doubts by voice**,
answered only from their own textbook with page references. Teachers can convert a worksheet into several languages in one click.

## Run it (demo mode, no keys needed)

**Windows:** double-click `run_windows.bat`. **macOS / Linux:** `bash run_mac_linux.sh`.
Both create a virtual environment, install the requirements, copy `.env.example` to `.env` and start the server.
Needs Python 3.10 or newer.

By hand:

```bash
cd vidyasetu
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-optional.txt               # optional: ChromaDB + OCR; the app works without them
python app.py
```

Open <http://localhost:5000> and click **Try the sample chapter**. With no keys the whole pipeline (glossary lock,
textbook search, quiz, voice, teacher mode, UI) runs for real but the AI text is a stand-in: the bundled sample
chapter is pre-translated into all 10 languages; anything else is tagged `[hi · demo]`. Demo mode never touches the network.

## Choose your AI

Copy `.env.example` to `.env` and fill in whichever keys you have. Each job is routed to a provider automatically:

| Job | Used for | Default when you have… |
|---|---|---|
| **Generate** | simplify, quizzes, textbook answers | IBM Granite → else Sarvam |
| **Translate** | translation, back-translation check | Sarvam → else IBM |
| **Embed** | textbook search, translation check | IBM Granite Embedding → else a built-in local embedder |
| **Speech** | the "Listen" buttons | Sarvam studio voice → else your browser's voices |

The badge in the header shows what is active, e.g. `Live · IBM Granite + Sarvam`. Force a choice with `GEN_PROVIDER`,
`TRANSLATE_PROVIDER`, `EMBED_PROVIDER`, `SPEECH_PROVIDER`.

### Sarvam AI (best for Indian-language translation and voice)
1. Create an API key in the Sarvam dashboard (docs.sarvam.ai) and set `SARVAM_API_KEY` in `.env`.
2. Restart. That's all: `sarvam-105b` handles generation and translation, and Bulbul speaks the lesson (Hindi, Marathi, Bengali, Tamil, Telugu, Gujarati, Kannada, Malayalam, Punjabi).
* **Rate limit:** the free Starter plan allows about 40 chat requests per minute, and the app throttles itself (`SARVAM_RPM`). A lesson is about 3 requests per paragraph, so a 10-paragraph lesson takes roughly a minute. Use the page-range boxes to do a chapter in parts.
* **Cost** (per Sarvam's price list; check your dashboard): chat is a few paise per paragraph, so a 10-paragraph lesson is roughly ₹1–3. **Voice is the main cost, about ₹3 per 1,000 characters.** Audio is cached on the server, so replaying a paragraph is free. To stop spending on voice, set `SPEECH_PROVIDER=browser`.
* If the studio voice ever fails, the app silently falls back to your browser's voice. If your Sarvam account uses a different voice name, set `SARVAM_TTS_SPEAKER`.

### IBM watsonx.ai / Granite
1. Set `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, `WATSONX_URL` for your region.
2. Run `python scripts/list_models.py` to see valid model ids, and set `GEN_MODEL` / `EMBED_MODEL` if the defaults aren't listed.
3. Granite 4.0 is not officially trained on most Indian languages, which is why `auto` sends translation to Sarvam when you have both. You can override with `TRANSLATE_PROVIDER=watsonx` and a stronger `TRANSLATE_MODEL`.

The **verify** step (translate back to English, compare with the simplified text) protects students whichever model is used:
weak paragraphs show "⚠ Please check this with your teacher" instead of looking correct. With the built-in local embedder
the comparison is word-based and its pass mark is lower (0.50 vs 0.80); set `QUALITY_THRESHOLD` to tune.

## Using the app

* **Design:** indigo and amber to match the pitch deck; every status is also written in words, so nothing depends on colour alone. Indian-script fonts (Noto Serif) are bundled in `static/fonts` so text renders correctly offline.
* **Make a lesson** from an uploaded file, pasted text, or just a topic. Pick the language and class in the workspace; the sample chapter button needs no setup.
* **Read** shows the lesson as an open book: English on the left, your language on the right, with a yellow play button on the spine of every paragraph. Switch between *Both*, your language only, or English only, change the text size, or show the textbook's original wording.
* **Exam terms** are underlined. Tap one to see it in both languages, hear it, or ask about it.
* **Listen to lesson** plays the whole lesson with a bar at the bottom (pause, previous, next, speed). The play button on a paragraph plays just that one.
* **Ask a doubt**, **Quiz** (keys 1 to 4 answer; missed questions can be retried) and **Progress** are tabs under the title. On a phone they sit in a bottom bar.
* **Delete a lesson** with the bin icon on its card. **Print** makes a clean two-column worksheet. Your choices (theme, text size, language, view) are remembered in the browser.

## How it maps to your deck

| Deck step | Where it lives |
|---|---|
| 1 Ingest (PyMuPDF / Tesseract) | `vidyasetu/ingest.py` |
| 2 Retrieve (embeddings + ChromaDB) | `vidyasetu/rag.py` (falls back to SQLite if Chroma is missing) |
| 3 Generate (simplify, quizzes) | `simplify()`, `make_quiz()` in `vidyasetu/pipeline.py` |
| 4 Translate + glossary lock | `translate_text()`, `vidyasetu/glossary.py` |
| 5 Verify & speak | `verify()`; voice via Sarvam (`/api/tts`) or the browser |
| Voice doubt loop | `ask()`: local language → English → RAG answer → local language |
| Subject glossary | `vidyasetu/glossary_seed.json` (+ your own in `data/glossary.json`) |
| Learner progress | SQLite (`data/vidyasetu.db`), "My progress" tab |
| Teacher mode | `teacher_convert()`, the Teacher tab |

## Things to know

* **Glossary:** the seed has 28 science and maths terms in Hindi and Marathi; the 7 terms of the sample chapter are also in the other 8 languages. Add terms or languages in
  `data/glossary.json` (`{"term": {"hi": "…", "ta": "…"}}`). A term is locked only for languages that have an entry.
* **Asking by voice** uses the browser's speech recognition (Chrome or Edge, on `localhost` or HTTPS). Sarvam speech-to-text is not wired in yet.
* **Photos / scanned pages** need the Tesseract program (Windows: the UB Mannheim installer; Linux: `sudo apt install tesseract-ocr`).
* **Languages:** the UI offers 10 (Urdu has no studio voice, so it uses the browser voice). Add more in `vidyasetu/languages.py`.
* **Limits:** up to 40 paragraphs per lesson (`MAX_PARAGRAPHS`), 25 MB uploads.
* This is a demo server (`python app.py`). For hosting, run it behind a WSGI server such as `gunicorn app:app`.
* Keep `.env` private: never commit it or share your keys.

## Tests

18 tests. On Windows, double-click `run_tests.bat`. Otherwise run each as its own command (each sets its own mode):

```bash
python -m unittest tests.test_smoke -v         # whole app in demo mode (10 tests)
python -m unittest tests.test_live_stub -v     # IBM watsonx client vs a local stand-in server (2 tests)
python -m unittest tests.test_sarvam_stub -v   # Sarvam chat, rate limit, voice cache, provider routing (6 tests)
```

The "stub" tests prove our request/response handling against a local stand-in for each service. They have **not** been run against the real
services, so do a first real lesson with your keys before the demo. Tests ignore your `.env`, so they can never spend your credits.

## Project layout

```
app.py                  Flask routes
run_windows.bat         one-click setup + start (Windows); run_mac_linux.sh for macOS/Linux; run_tests.bat
vidyasetu/              pipeline, provider clients (sarvam.py, watsonx.py), glossary, RAG, demo-mode AI
                        (sample_translations.py = the sample chapter pre-translated for demo mode)
static/                 the web app (HTML/CSS/JS, installable PWA; fonts/ and fonts.css are bundled fonts)
samples/                sample chapter
scripts/list_models.py  list valid watsonx model ids
tests/
```
