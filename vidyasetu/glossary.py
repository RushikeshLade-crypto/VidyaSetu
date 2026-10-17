"""Glossary lock: exam terms are swapped for placeholders before translation and
restored afterwards as "native (english)", so a model can never mistranslate
them. Only terms that have an entry for the target language are locked; for
other terms the model translates freely."""
import json
import re
from functools import lru_cache
from pathlib import Path

from . import config

SEED_FILE = Path(__file__).with_name("glossary_seed.json")
USER_FILE = config.DATA_DIR / "glossary.json"  # optional; same format, merged over the seed

TOKEN_RE = re.compile(r"\[\[\s*T(\d+)\s*\]\]")


@lru_cache(maxsize=1)
def _load() -> dict:
    terms = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    if USER_FILE.exists():
        try:
            for k, v in json.loads(USER_FILE.read_text(encoding="utf-8")).items():
                terms.setdefault(k.lower(), {}).update(v)
        except (ValueError, OSError):
            pass
    return {k.lower(): v for k, v in terms.items()}


def reload() -> None:
    _load.cache_clear()


def native(term: str, lang: str) -> str | None:
    return _load().get(term.lower(), {}).get(lang)


def lock(text: str, lang: str) -> tuple[str, dict[str, str]]:
    """Return (text with [[T1]] placeholders, {"[[T1]]": "photosynthesis"})."""
    terms = [t for t, tr in _load().items() if lang in tr]
    if not terms:
        return text, {}
    terms.sort(key=len, reverse=True)
    pattern = re.compile(
        r"(?<![A-Za-z])(" + "|".join(re.escape(t) for t in terms) + r")(?:s|es)?(?![A-Za-z])",
        re.IGNORECASE,
    )
    mapping: dict[str, str] = {}
    by_term: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        term = m.group(1).lower()
        if term not in by_term:
            token = f"[[T{len(by_term) + 1}]]"
            by_term[term] = token
            mapping[token] = term
        return by_term[term]

    return pattern.sub(repl, text), mapping


def normalise_tokens(text: str) -> str:
    """Models sometimes add spaces inside the brackets; make them canonical."""
    return TOKEN_RE.sub(lambda m: f"[[T{m.group(1)}]]", text)


def unlock(text: str, mapping: dict[str, str], lang: str) -> str:
    """Replace placeholders; first use of a term shows 'native (english)'."""
    seen: set[str] = set()

    def repl(m: re.Match) -> str:
        token = f"[[T{m.group(1)}]]"
        term = mapping.get(token)
        if term is None:
            return ""
        nat = native(term, lang) or term
        if token in seen:
            return nat
        seen.add(token)
        return f"{nat} ({term})"

    return TOKEN_RE.sub(repl, text)


def used_terms(mapping: dict[str, str], lang: str) -> list[dict]:
    return [{"term": t, "native": native(t, lang) or t} for t in mapping.values()]
