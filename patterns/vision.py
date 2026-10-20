"""Part 29d: vision labelling.  Each labelled pattern is drawn as a small PNG and shown, with its
text label, to a local vision model, whose description is stored as ``label.visual`` and embedded.

    python -m patterns.build --vision [--vision-provider ollama|gemini] [--vision-model M]
        [--max-calls N]

Opt-in.  ``ollama`` (default) runs ``qwen2.5vl:7b`` locally; ``gemini`` sends the PNG to
``gemini-3.5-flash-lite`` (key ``GEMINI_API_KEY`` from the environment or ``.env``), paced to
12 requests a minute, and switches to the local model when Gemini keeps failing.  When no model
can run the build keeps the text-only labels and warns once.  Tests use a fake describer; nothing here runs without ``--vision``.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from llm.providers import base
from llm.providers.gemini import DEFAULT_BASE_URL, adapt_schema

from patterns.store import PatternStore

DEFAULT_VISION_MODEL = "qwen2.5vl:7b"
DEFAULT_GEMINI_VISION_MODEL = "gemini-3.5-flash-lite"
GEMINI_RPM = 12
RETRY_STATUSES = {429, 500, 502, 503, 504}
CELL_PX = 16

# grid colours: air, rock, hazard, start, exit, enemy, pickup (anything else: other entity)
COLOURS = {".": (235, 240, 248), "#": (70, 62, 58), "^": (220, 50, 40), "S": (40, 170, 70),
           "X": (240, 190, 30), "E": (150, 50, 190), "P": (30, 150, 230)}
OTHER = (255, 120, 200)

VISION_PROMPT = """\
This image is a fragment of a 2-D platformer level, drawn as a grid: dark brown = solid rock,
light = air, red = hazard (spikes, lava), green = player start, yellow = exit, purple = enemy,
blue = pickup, pink = other object.  The top of the image is up; the player walks on the top of
rock.  Measured from the grid (exact; trust these over the label): {facts}.  Do not mention
any kind whose count is 0.  A text-only labeller already wrote this label (it may be wrong): {label}

Look at the picture and output ONE JSON object:
  "visual": 1-2 plain sentences on the layout the player meets, as a designer would: where
            things are (left/right, top/bottom), the shape (flat run, staircase, scattered
            platforms, tower, walls, closed room) and where any hazards and enemies sit.  Say
            only what is in the picture; no filler such as "challenge" or "precise",
  "tags": 2-5 lowercase words for retrieval (layout words, not "platformer"),
  "label_ok": false if the text label clearly contradicts the picture (for example it names
              hazards that are not there), else true.\
"""

RETRY_PROMPT = ("\n\nYour previous answer mentioned {kinds}, but this fragment has none.  Describe it "
                "again without them.")

# words that claim a kind is present; a clause with a negation ("no enemies") claims nothing
CLAIM_WORDS = {"hazards": r"hazard|spike|lava|fire|acid|trap|thorn",
               "enemies": r"enem|monster|foe|creature",
               "pickups": r"pickup|pick-up|coin|collect|gem|item"}
NEGATION = re.compile(r"\b(no|not|none|without|nor|free of|absent)\b", re.I)


def grid_facts(ascii_grid: str) -> Dict[str, Any]:
    """What the grid holds, counted (shown to the model and used by the post-check)."""
    rows = [r for r in ascii_grid.split("\n") if r]
    text = "".join(rows)
    return {"size": f"{max((len(r) for r in rows), default=0)}x{len(rows)} tiles",
            "hazards": text.count("^"), "enemies": text.count("E"), "pickups": text.count("P"),
            "start": "S" in text, "exit": "X" in text}


def false_claims(visual: str, facts: Dict[str, Any]) -> List[str]:
    """Kinds the description says are present although the grid has none of them."""
    clauses = [x for x in re.split(r"(?<=[.!?;])\s+|\b(?:but|while|whereas)\b", visual)
               if x and not NEGATION.search(x)]
    return [kind for kind, words in CLAIM_WORDS.items()
            if not facts[kind] and any(re.search(rf"\b({words})", x, re.I) for x in clauses)]


class VisualLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visual: str
    tags: List[str] = Field(default_factory=list)
    label_ok: bool = True


@dataclass
class VisionReport:
    described: int = 0
    cached: int = 0
    calls: int = 0
    disagreed: int = 0  # label_ok == false
    retried: int = 0  # descriptions that named a kind the grid lacks and were asked again
    stopped: bool = False
    skipped: Optional[str] = None  # why vision did not run (model missing, ...)
    fell_back: Optional[str] = None  # why Gemini was replaced by the local model
    errors: List[str] = field(default_factory=list)


def render_png(ascii_grid: str, cell: int = CELL_PX) -> bytes:
    """The pattern's ASCII grid as a PNG (row 0 = top)."""
    from PIL import Image

    rows = [r for r in ascii_grid.split("\n") if r]
    w = max((len(r) for r in rows), default=1)
    img = Image.new("RGB", (w, max(len(rows), 1)), COLOURS["."])
    px = img.load()
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            px[x, y] = COLOURS.get(ch, OTHER)
    img = img.resize((w * cell, max(len(rows), 1) * cell), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _host() -> str:
    return (os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")


def model_available(model: str) -> Optional[str]:
    """None when Ollama has *model*, else why not."""
    try:
        with urllib.request.urlopen(f"{_host()}/api/tags", timeout=5) as resp:  # noqa: S310
            names = {m.get("name") for m in json.loads(resp.read().decode()).get("models", [])}
    except Exception as exc:  # no server
        return f"Ollama is not reachable ({exc})"
    if model in names or f"{model}:latest" in names:
        return None
    return f"vision model {model} is not pulled (ollama pull {model})"


def ollama_describer(model: str, timeout: float = 300.0) -> Callable[[bytes, str], dict]:
    """A describer: (png, prompt) -> the parsed JSON reply, schema-enforced."""
    def describe(png: bytes, prompt: str) -> dict:
        body = {"model": model, "stream": False, "format": VisualLabel.model_json_schema(),
                "options": {"temperature": 0.2},
                "messages": [{"role": "user", "content": prompt,
                              "images": [base64.b64encode(png).decode()]}]}
        req = urllib.request.Request(f"{_host()}/api/chat", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local server)
            return json.loads(json.loads(resp.read().decode())["message"]["content"])
    describe.model_name = f"ollama:{model}"  # type: ignore[attr-defined]
    return describe


def _gemini_key() -> str:
    """``GEMINI_API_KEY`` from the environment, else from the user or repo ``.env``."""
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    for env in (Path.home() / "Library" / "Application Support" / "GLDtk" / ".env",
                Path(__file__).resolve().parent.parent / ".env"):
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                key, _, value = line.strip().removeprefix("export ").partition("=")
                if key.strip() == "GEMINI_API_KEY" and value.strip():
                    return value.strip().strip("\"'")
    return ""


def gemini_describer(
    model: str = DEFAULT_GEMINI_VISION_MODEL, *, api_key: Optional[str] = None,
    rpm: float = GEMINI_RPM, retries: int = 4, backoff: float = 10.0, timeout: float = 120.0,
    post: Callable[..., dict] = base.http_json_post, sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[bytes, str], dict]:
    """A Gemini describer: the PNG as inline data, schema-enforced JSON, at most *rpm* calls a
    minute; 429/5xx/timeouts back off (10, 20, 40, 80 s) before the error is raised."""
    key = api_key if api_key is not None else _gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set (add it to .env)")
    url = f"{DEFAULT_BASE_URL}/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}  # never in the URL
    last = [float("-inf")]

    def describe(png: bytes, prompt: str) -> dict:
        payload = {"contents": [{"role": "user", "parts": [
                       {"text": prompt},
                       {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode()}}]}],
                   "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2,
                                        "responseJsonSchema": adapt_schema(VisualLabel.model_json_schema())}}
        for attempt in range(retries + 1):
            wait = last[0] + 60.0 / rpm - clock()
            if wait > 0:
                sleep(wait)
            last[0] = clock()
            try:
                resp = post(url, headers, payload, timeout)
            except base.ProviderError as err:
                if attempt == retries or not (err.status in RETRY_STATUSES or err.timed_out):
                    raise
                sleep(backoff * 2 ** attempt)
                continue
            return json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])
        raise AssertionError("unreachable")

    describe.model_name = f"gemini:{model}"  # type: ignore[attr-defined]
    return describe


def with_fallback(primary: Callable[[bytes, str], dict], make_fallback: Callable[[], Optional[Callable]],
                  warn: Callable[[str], None], after: int = 3) -> Callable[[bytes, str], dict]:
    """*primary* until it fails with a provider error *after* times in a row, then the
    fallback for the rest of the run (said once through *warn*)."""
    state = {"fn": primary, "fails": 0, "switched": None}

    def describe(png: bytes, prompt: str) -> dict:
        try:
            out = state["fn"](png, prompt)
        except base.ProviderError as err:
            if state["fn"] is not primary:
                raise
            state["fails"] += 1
            if state["fails"] < after:
                raise
            fallback = make_fallback()
            if fallback is None:
                raise
            state["fn"], state["switched"] = fallback, f"{primary.model_name} kept failing ({err})"
            describe.model_name = fallback.model_name  # type: ignore[attr-defined]
            warn(f"{state['switched']}; switching to {fallback.model_name}")
            return fallback(png, prompt)
        if state["fn"] is primary:
            state["fails"] = 0
        return out

    describe.model_name = primary.model_name  # type: ignore[attr-defined]
    describe.state = state  # type: ignore[attr-defined]
    return describe


def _text_label(lab: dict) -> str:
    return json.dumps({k: lab.get(k) for k in ("title", "purpose", "mechanics", "structure_type")})


def describe_pending(
    store: PatternStore,
    describer: Optional[Callable[[bytes, str], dict]] = None,
    *,
    model: Optional[str] = None,
    provider: str = "ollama",
    max_calls: Optional[int] = None,
    warn: Callable[[str], None] = lambda m: print("warning:", m, file=sys.stderr),
) -> VisionReport:
    """Add ``label.visual`` to every labelled pattern that has none (saves every 10)."""
    rep = VisionReport()
    todo = [r for r in store if r.get("label") and r.get("kind", "pattern") == "pattern"
            and r.get("ascii")]
    rep.cached = sum(1 for r in todo if r["label"].get("visual"))
    todo = [r for r in todo if not r["label"].get("visual")]
    if describer is None:
        local = DEFAULT_VISION_MODEL if provider == "gemini" else (model or DEFAULT_VISION_MODEL)
        make_local = lambda: None if model_available(local) else ollama_describer(local)  # noqa: E731
        if provider == "gemini" and _gemini_key():
            describer = with_fallback(gemini_describer(model or DEFAULT_GEMINI_VISION_MODEL),
                                      make_local, warn)
        else:
            if provider == "gemini":
                warn(f"GEMINI_API_KEY is not set; using {local}")
            why = model_available(local)
            if why:
                rep.skipped = why
                warn(f"{why}; keeping text-only labels")
                return rep
            describer = ollama_describer(local)
    for rec in todo:
        if max_calls is not None and rep.calls >= max_calls:
            rep.stopped = True
            break
        facts = grid_facts(rec["ascii"])
        prompt = VISION_PROMPT.format(facts=json.dumps(facts), label=_text_label(rec["label"]))
        png = render_png(rec["ascii"])
        rep.calls += 1
        try:
            out = VisualLabel.model_validate(describer(png, prompt))
            wrong = false_claims(out.visual, facts)
            if wrong:  # one retry with the correction, then drop the description
                rep.retried += 1
                rep.calls += 1
                out = VisualLabel.model_validate(
                    describer(png, prompt + RETRY_PROMPT.format(kinds=" and ".join(wrong))))
                wrong = false_claims(out.visual, facts)
                if wrong:
                    raise ValueError(f"describes {' and '.join(wrong)} the grid does not have")
        except Exception as exc:  # one bad reply never stops the run
            rep.errors.append(f"{rec['id']}: {exc}")
            continue
        rec["label"]["visual"] = out.visual.strip()
        rec["label"]["visual_tags"] = [t.lower() for t in out.tags]
        rec["label"]["visual_ok"] = out.label_ok
        rec["label"]["visual_model"] = getattr(describer, "model_name", None)
        rep.disagreed += not out.label_ok
        rep.described += 1
        if rep.described % 10 == 0:
            store.save()
    if rep.described:
        store.save()
    rep.fell_back = (getattr(describer, "state", None) or {}).get("switched")
    return rep


__all__ = ["DEFAULT_GEMINI_VISION_MODEL", "DEFAULT_VISION_MODEL", "VISION_PROMPT", "VisionReport", "VisualLabel", "describe_pending",
           "false_claims", "gemini_describer", "with_fallback", "grid_facts", "model_available", "ollama_describer", "render_png"]
