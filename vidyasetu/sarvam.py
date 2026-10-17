"""REST client for Sarvam AI: chat (sarvam-105b) and text-to-speech (Bulbul).
Docs: https://docs.sarvam.ai   Auth header: api-subscription-key"""
import base64
import re
import threading
import time
from collections import deque

import requests

from . import config


class SarvamError(RuntimeError):
    pass


class Throttle:
    """Allow at most `rpm` calls per `window` seconds (blocks until a slot is free)."""

    def __init__(self, rpm: int, window: float = 60.0):
        self.rpm, self.window = max(1, rpm), window
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            while True:
                now = time.monotonic()
                while self.calls and now - self.calls[0] >= self.window:
                    self.calls.popleft()
                if len(self.calls) < self.rpm:
                    self.calls.append(now)
                    return
                time.sleep(max(0.05, self.window - (now - self.calls[0])))


_chat_throttle = Throttle(config.SARVAM_RPM)
_tts_throttle = Throttle(config.SARVAM_TTS_RPM)


def _post(path: str, body: dict, throttle: Throttle, timeout: int = 120) -> dict:
    if not config.SARVAM_API_KEY:
        raise SarvamError("SARVAM_API_KEY is not set (see .env.example).")
    url = f"{config.SARVAM_BASE_URL}{path}"
    headers = {"api-subscription-key": config.SARVAM_API_KEY, "Content-Type": "application/json"}
    last = "unknown error"
    for attempt in range(5):
        throttle.wait()
        try:
            r = requests.post(url, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last = str(exc)
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (401, 403):
            raise SarvamError("Sarvam rejected the API key (check SARVAM_API_KEY, and that you have credits left).")
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"{r.status_code} {r.text[:160]}"
            try:
                delay = float(r.headers.get("Retry-After", ""))
            except ValueError:
                delay = 2.0 * (attempt + 1)
            time.sleep(min(delay, 30))
            continue
        if not r.ok:
            raise SarvamError(f"Sarvam returned {r.status_code}: {r.text[:400]}")
        return r.json()
    raise SarvamError(f"Sarvam is not responding ({last})")


def chat(messages: list[dict], max_tokens: int = 1024, temperature: float = 0.2, json_mode: bool = False) -> str:
    body = {
        "model": config.SARVAM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": min(int(max_tokens), config.SARVAM_MAX_TOKENS),
        "reasoning_effort": None,  # thinking is on by default and would spend our token budget
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = _post("/v1/chat/completions", body, _chat_throttle)
    try:
        choice = data["choices"][0]
        text = (choice["message"].get("content") or "").strip()
    except (KeyError, IndexError):
        raise SarvamError(f"Unexpected Sarvam response: {str(data)[:300]}")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if not text and choice.get("finish_reason") == "length":
        raise SarvamError("Sarvam ran out of tokens before answering. Try a shorter page range.")
    return text


def tts(text: str, language_code: str) -> bytes:
    """Return WAV audio bytes for `text` spoken in `language_code` (e.g. hi-IN)."""
    body = {
        "text": text[:2500],
        "language_code": language_code,
        "speaker": config.SARVAM_TTS_SPEAKER,
        "model": config.SARVAM_TTS_MODEL,
    }
    data = _post("/text-to-speech", body, _tts_throttle)
    audios = data.get("audios") or []
    if not audios:
        raise SarvamError("Sarvam returned no audio.")
    try:  # documented format: the pieces are slices of one base64 string
        return base64.b64decode("".join(audios))
    except (ValueError, TypeError):
        try:  # tolerate independently-encoded pieces
            return b"".join(base64.b64decode(a) for a in audios)
        except (ValueError, TypeError):
            raise SarvamError("Sarvam returned audio we could not decode.")
