"""Pluggable text embedders: ``hash`` (offline, deterministic), ``gemini`` and ``ollama``.

Only ``hash`` is used by the tests.  ``gemini`` reads ``GEMINI_API_KEY`` (the git-ignored
``.env``); ``ollama`` talks to a local server (``nomic-embed-text`` by default: pull it
yourself, nothing is downloaded here).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.request
from typing import Any, Dict, List, Optional

_WORD = re.compile(r"[a-z0-9_]+")


def _normalise(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


class HashEmbedder:
    """Bag-of-words hashing embedder: each word (and word pair) adds to one of ``dim`` buckets."""

    name = "hash"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> List[float]:
        words = _WORD.findall(text.lower())
        v = [0.0] * self.dim
        toks = [(w, 1.0) for w in words] + [(a + " " + b, 0.5) for a, b in zip(words, words[1:])]
        for tok, weight in toks:
            h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "big")
            v[h % self.dim] += weight if (h >> 40) & 1 else -weight
        return _normalise(v)


def _post(url: str, body: dict, headers: Optional[dict] = None, timeout: float = 60.0) -> Any:
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed URLs)
        return json.loads(resp.read().decode("utf-8"))


class GeminiEmbedder:
    name = "gemini"
    dim = 0  # known after the first call

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        self.model = model or os.environ.get("GLDTK_EMBED_MODEL", "gemini-embedding-001")
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (add it to .env)")
        out: List[List[float]] = []
        for i in range(0, len(texts), 50):
            chunk = texts[i:i + 50]
            doc = _post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:batchEmbedContents",
                {"requests": [{"model": f"models/{self.model}", "content": {"parts": [{"text": t}]}}
                              for t in chunk]},
                {"x-goog-api-key": self.api_key})
            out += [_normalise(list(e["values"])) for e in doc["embeddings"]]
        self.dim = len(out[0]) if out else 0
        return out


class OllamaEmbedder:
    name = "ollama"
    dim = 0

    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None) -> None:
        self.model = model or os.environ.get("GLDTK_EMBED_MODEL", "nomic-embed-text")
        self.base_url = (base_url or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")

    def embed(self, texts: List[str]) -> List[List[float]]:
        doc = _post(f"{self.base_url}/api/embed", {"model": self.model, "input": texts})
        out = [_normalise(list(v)) for v in doc["embeddings"]]
        self.dim = len(out[0]) if out else 0
        return out


def make_embedder(name: str = "hash") -> Any:
    name = (name or "hash").lower()
    if name == "hash":
        return HashEmbedder()
    if name == "gemini":
        return GeminiEmbedder()
    if name == "ollama":
        return OllamaEmbedder()
    raise ValueError(f"unknown embedder {name!r} (hash | gemini | ollama)")


def cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # vectors are unit length


def embed_text(rec: Dict[str, Any]) -> str:
    """The text a pattern is embedded as: its label when it has one, plus geometry words."""
    lab = rec.get("label") or {}
    f = rec.get("features") or {}
    parts = [lab.get("title", ""), lab.get("purpose", ""), lab.get("mood", ""),
             " ".join(lab.get("mechanics") or []), lab.get("structure_type") or f.get("structure", ""),
             " ".join(lab.get("tags") or [])]
    if f.get("hazards"):
        parts.append("hazard spikes lava")
    if f.get("enemies"):
        parts.append("enemy combat")
    if f.get("max_gap", 0) >= 4:
        parts.append("wide gap jump")
    if f.get("height", 0) > f.get("width", 1):
        parts.append("vertical climb shaft")
    return " ".join(p for p in parts if p)


def sync_vectors(store: Any, embedder: Any) -> int:
    """Embed every accepted pattern whose vector is missing or stale; returns how many."""
    if store.embedder and store.embedder.get("name") != embedder.name:
        store.vectors.clear()  # vectors of another embedder are not comparable
    todo, texts = [], []
    for pid, rec in store.patterns.items():
        if not rec.get("accepted"):
            continue
        text = embed_text(rec)
        th = hashlib.md5(text.encode()).hexdigest()
        if store.vectors.get(pid, {}).get("text_hash") != th:
            todo.append((pid, th))
            texts.append(text)
    if todo:
        vecs = embedder.embed(texts)
        for (pid, th), v in zip(todo, vecs):
            store.vectors[pid] = {"text_hash": th, "vec": [round(x, 6) for x in v]}
        store.embedder = {"name": embedder.name, "dim": len(vecs[0])}
    return len(todo)


__all__ = ["GeminiEmbedder", "HashEmbedder", "OllamaEmbedder", "cosine", "embed_text",
           "make_embedder", "sync_vectors"]
