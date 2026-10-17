"""Demo mode: stand-in AI so the whole app runs without watsonx.ai credentials.

Nothing here is real AI. Simplify returns the text unchanged, translation only
works for the bundled sample chapter (pre-written in all 10 languages) and is
otherwise tagged "[xx · demo]", and answers are the best-matching textbook passage. The goal is
to exercise the real pipeline (glossary lock, RAG, quiz, UI) end to end."""
import json
import re
import zlib

from . import config, glossary, languages
from .sample_translations import EXTRA, NOT_FOUND
from .localembed import embed  # noqa: F401  (re-exported: demo mode uses the local embedder)

SAMPLE_FILE = config.BASE_DIR / "samples" / "photosynthesis.txt"

# English sentence -> translation, with [[term]] marking glossary terms.
_SAMPLE = {
    "Plants make their own food.": {
        "hi": "पौधे अपना भोजन स्वयं बनाते हैं।",
        "mr": "झाडे स्वतःचे अन्न स्वतः तयार करतात.",
    },
    "This process is called photosynthesis.": {
        "hi": "इस प्रक्रिया को [[photosynthesis]] कहते हैं।",
        "mr": "या प्रक्रियेला [[photosynthesis]] म्हणतात.",
    },
    "It takes place mostly in the leaves of a plant.": {
        "hi": "यह प्रक्रिया मुख्य रूप से पौधे की पत्तियों में होती है।",
        "mr": "ही प्रक्रिया मुख्यत्वे झाडाच्या पानांमध्ये घडते.",
    },
    "Leaves contain a green substance called chlorophyll.": {
        "hi": "पत्तियों में एक हरा पदार्थ होता है जिसे [[chlorophyll]] कहते हैं।",
        "mr": "पानांमध्ये हिरवा पदार्थ असतो, त्याला [[chlorophyll]] म्हणतात.",
    },
    "Chlorophyll captures energy from sunlight.": {
        "hi": "[[chlorophyll]] सूर्य के प्रकाश से [[energy]] ग्रहण करता है।",
        "mr": "[[chlorophyll]] सूर्यप्रकाशातून [[energy]] मिळवते.",
    },
    "The leaf also takes in carbon dioxide from the air through tiny holes called stomata.": {
        "hi": "पत्ती हवा से [[carbon dioxide]] भी लेती है, जो छोटे-छोटे छिद्रों से होकर आती है; इन छिद्रों को [[stomata]] कहते हैं।",
        "mr": "पान लहान छिद्रांमधून हवेतील [[carbon dioxide]] घेते, या छिद्रांना [[stomata]] म्हणतात.",
    },
    "The plant uses sunlight, water and carbon dioxide to make glucose, a type of sugar.": {
        "hi": "पौधा सूर्य के प्रकाश, जल और [[carbon dioxide]] की सहायता से [[glucose]] बनाता है, जो एक प्रकार की शर्करा है।",
        "mr": "झाड सूर्यप्रकाश, पाणी आणि [[carbon dioxide]] यांचा वापर करून [[glucose]] तयार करते, जी एक प्रकारची साखर आहे.",
    },
    "Oxygen is released into the air as a by-product.": {
        "hi": "उप-उत्पाद के रूप में [[oxygen]] हवा में छोड़ी जाती है।",
        "mr": "उप-उत्पादन म्हणून [[oxygen]] हवेत सोडला जातो.",
    },
    "Animals and humans breathe in this oxygen.": {
        "hi": "जंतु और मनुष्य इसी [[oxygen]] में साँस लेते हैं।",
        "mr": "प्राणी आणि माणसे हाच [[oxygen]] श्वासावाटे घेतात.",
    },
    "Photosynthesis is important because it gives food to almost all living things.": {
        "hi": "[[photosynthesis]] महत्वपूर्ण है क्योंकि यह लगभग सभी जीवों को भोजन देती है।",
        "mr": "[[photosynthesis]] महत्त्वाचे आहे कारण ते जवळजवळ सर्व सजीवांना अन्न पुरवते.",
    },
    "It also keeps the amount of oxygen and carbon dioxide in the air balanced.": {
        "hi": "यह हवा में [[oxygen]] और [[carbon dioxide]] की मात्रा को भी संतुलित रखती है।",
        "mr": "तसेच हवेतील [[oxygen]] आणि [[carbon dioxide]] चे प्रमाण संतुलित ठेवते.",
    },
}
for _en, _trs in EXTRA.items():  # the other eight languages
    _SAMPLE[_en].update(_trs)
_SAMPLE.update(NOT_FOUND)
BLANK = "______"


def _build_fixtures() -> dict[str, dict[str, str]]:
    fx: dict[str, dict[str, str]] = {code: {} for code in languages.LANGUAGES}
    for en, trs in _SAMPLE.items():
        for lang, tr in trs.items():
            fx.setdefault(lang, {})[en] = tr
            for term in set(re.findall(r"\[\[([a-z ]+)\]\]", tr)):
                en_blank = re.sub(rf"(?<![A-Za-z]){re.escape(term)}", BLANK, en, flags=re.I)
                if en_blank != en:
                    fx[lang][en_blank] = tr.replace(f"[[{term}]]", BLANK)
    return fx


FIXTURES = _build_fixtures()


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _translate(text: str, locked: str, lang: str, mapping: dict[str, str]) -> str:
    if re.fullmatch(r"\s*\[\[T\d+\]\]\s*", locked):  # just a locked term (e.g. a quiz option)
        return locked.strip()
    fx = FIXTURES.get(lang, {})
    parts = []
    for s in sentences(text):
        tr = fx.get(s.strip())
        if tr is None:
            return f"[{lang} · demo] {locked}"
        parts.append(tr)
    inverse = {term: token for token, term in mapping.items()}
    joined = " ".join(parts)
    return re.sub(
        r"\[\[([a-z ]+)\]\]",
        lambda m: inverse.get(m.group(1), glossary.native(m.group(1), lang) or m.group(1)),
        joined,
    )


def _quiz(text: str, n: int, lang: str | None = None) -> str:
    # distractors come from terms the glossary has in this language, so options are never left untranslated
    terms = [t for t, tr in glossary._load().items() if not lang or lang in tr] or list(glossary._load().keys())
    questions = []
    for sent in sentences(text):
        found = []
        for t in terms:
            m = re.search(rf"(?<![A-Za-z]){re.escape(t)}(?![A-Za-z])", sent, re.I)
            if m:
                found.append((m.start(), t))
        if not found:
            continue
        term = min(found)[1]
        stem = re.sub(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", BLANK, sent, flags=re.I)
        others = sorted((t for t in terms if t != term), key=lambda t: zlib.crc32((term + t).encode()))[:3]
        options = sorted([term] + others, key=lambda t: zlib.crc32((t + sent).encode()))
        questions.append(
            {
                "question": stem,
                "options": [o[:1].upper() + o[1:] for o in options],
                "answer_index": options.index(term),
                "explanation": sent,
            }
        )
        if len(questions) >= n:
            break
    return json.dumps({"questions": questions})


def handle(task: str, p: dict) -> str:
    if task == "simplify":
        return p["text"]
    if task == "translate":
        return _translate(p["text"], p["locked"], p["lang"], p["mapping"])
    if task == "translate_many":
        lines = [
            f"<<{i}>> " + _translate(it["text"], it["locked"], p["lang"], it["mapping"])
            for i, it in enumerate(p["items"], 1)
        ]
        return "\n".join(lines)
    if task == "back_translate":
        return p["reference"]
    if task == "q_to_en":
        found = []
        for term, tr in glossary._load().items():
            if any(native and native in p["question"] for native in tr.values()):
                found.append(term)
        return ("What is " + " and ".join(found) + "?") if found else p["question"]
    if task == "answer":
        hits = p["hits"]
        if not hits or hits[0]["score"] < 0.12:
            return "NOT_IN_TEXTBOOK"
        return hits[0]["text"]
    if task == "quiz":
        return _quiz(p["text"], p["n"], p.get("lang"))
    if task == "topic_lesson":
        if "photosynth" in p["topic"].lower():
            return SAMPLE_FILE.read_text(encoding="utf-8")
        return (
            f"Demo mode cannot write a lesson about {p['topic']}.\n\n"
            "Add your IBM watsonx.ai or Sarvam AI key to .env to let the AI write it, or try the topic \"Photosynthesis\"."
        )
    raise ValueError(f"unknown mock task {task}")
