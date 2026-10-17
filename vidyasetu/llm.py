"""One entry point for every model call. Each task is routed to the provider chosen in
.env (watsonx / sarvam / mock), so Granite, Sarvam and demo mode share one pipeline."""
from . import config, localembed, mock, sarvam, watsonx

GEN_TASKS = {"simplify", "topic_lesson", "answer", "quiz"}
TRANSLATE_TASKS = {"translate", "translate_many", "back_translate", "q_to_en"}


def provider_for(task: str) -> str:
    return config.GEN_PROVIDER if task in GEN_TASKS else config.TRANSLATE_PROVIDER


def is_mock() -> bool:
    """True when the whole app is running on placeholder AI."""
    return config.MOCK


def chat(task: str, system: str, user: str, *, model: str | None = None, max_tokens: int = 1024,
         temperature: float = 0.2, payload: dict | None = None, json_mode: bool = False) -> str:
    provider = provider_for(task)
    if provider == "mock":
        return mock.handle(task, payload or {})
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if provider == "sarvam":
        return sarvam.chat(messages, max_tokens, temperature, json_mode)
    default_model = config.GEN_MODEL if task in GEN_TASKS else config.TRANSLATE_MODEL
    return watsonx.chat(model or default_model, messages, max_tokens, temperature)


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return watsonx.embed(texts) if config.EMBED_PROVIDER == "watsonx" else localembed.embed(texts)
