"""Thin REST client for IBM watsonx.ai (chat + embeddings). No SDK needed."""
import threading
import time

import requests

from . import config

_token = {"value": None, "expires": 0.0}
_lock = threading.Lock()


class WatsonxError(RuntimeError):
    pass


def _access_token() -> str:
    with _lock:
        if _token["value"] and time.time() < _token["expires"] - 60:
            return _token["value"]
        if not config.HAVE_KEYS:
            raise WatsonxError("WATSONX_API_KEY and WATSONX_PROJECT_ID are not set (see .env.example).")
        r = requests.post(
            config.IAM_URL,
            data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": config.WATSONX_API_KEY},
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            timeout=30,
        )
        if not r.ok:
            raise WatsonxError(f"IBM Cloud login failed ({r.status_code}). Check WATSONX_API_KEY.")
        data = r.json()
        _token["value"] = data["access_token"]
        _token["expires"] = float(data.get("expiration", time.time() + 3000))
        return _token["value"]


def _post(path: str, version: str, body: dict, timeout: int = 120) -> dict:
    url = f"{config.WATSONX_URL}{path}?version={version}"
    last = "unknown error"
    for attempt in range(4):
        try:
            r = requests.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {_access_token()}", "Accept": "application/json"},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last = str(exc)
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 401:
            _token["value"] = None  # force a fresh token on the next try
            last = "401 unauthorised"
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"{r.status_code} {r.text[:160]}"
            time.sleep(1.5 * (attempt + 1))
            continue
        if not r.ok:
            raise WatsonxError(f"watsonx.ai returned {r.status_code}: {r.text[:400]}")
        return r.json()
    raise WatsonxError(f"watsonx.ai is not responding ({last})")


def chat(model: str, messages: list[dict], max_tokens: int = 1024, temperature: float = 0.2) -> str:
    body = {
        "model_id": model,
        "project_id": config.WATSONX_PROJECT_ID,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = _post("/ml/v1/text/chat", config.CHAT_API_VERSION, body)
    try:
        return (data["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError):
        raise WatsonxError(f"Unexpected chat response: {str(data)[:300]}")


def embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), 50):
        batch = texts[i : i + 50]
        body = {
            "inputs": batch,
            "model_id": config.EMBED_MODEL,
            "project_id": config.WATSONX_PROJECT_ID,
            "parameters": {"truncate_input_tokens": 512},
        }
        data = _post("/ml/v1/text/embeddings", config.EMBED_API_VERSION, body)
        out.extend(item["embedding"] for item in data["results"])
    return out


def list_chat_models() -> list[str]:
    r = requests.get(
        f"{config.WATSONX_URL}/ml/v1/foundation_model_specs",
        params={"version": "2024-10-10", "filters": "function_text_chat"},
        timeout=30,
    )
    r.raise_for_status()
    return sorted(m["model_id"] for m in r.json().get("resources", []))


def list_embedding_models() -> list[str]:
    r = requests.get(
        f"{config.WATSONX_URL}/ml/v1/foundation_model_specs",
        params={"version": "2024-10-10", "filters": "function_embedding"},
        timeout=30,
    )
    r.raise_for_status()
    return sorted(m["model_id"] for m in r.json().get("resources", []))
