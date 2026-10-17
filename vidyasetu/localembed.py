"""Tiny built-in text embedder (hashed bag-of-words). No model, no network, no cost.

Good enough to find the right textbook paragraph for a question in English, and to
roughly compare a sentence with its back-translation. Used when no neural embedding
service (watsonx) is configured."""
import hashlib
import re

import numpy as np

_STOP = set("a an the of in on to is are was were be it its this that and or for from with as by at which what who how why do does did can".split())


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", text.lower()) if w not in _STOP and len(w) > 1]


def embed(texts: list[str], dim: int = 384) -> list[list[float]]:
    out = []
    for t in texts:
        v = np.zeros(dim, dtype=np.float32)
        for w in tokens(t):
            h = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
            v[h % dim] += 1.0 if (h >> 20) & 1 else -1.0
            v[(h >> 8) % dim] += 0.5
        norm = np.linalg.norm(v)
        out.append((v / norm if norm else v).tolist())
    return out
