"""v4 Part 29d: vision labelling of patterns (offline, fake describer)."""

from __future__ import annotations

import io

from PIL import Image

from patterns import vision
from patterns.embed import embed_text
from patterns.store import PatternStore
from patterns.vision import COLOURS, CELL_PX, describe_pending, render_png

GRID = "....X\n.S...\n###^#"


def _store(tmp_path, n=3):
    store = PatternStore(tmp_path)
    for i in range(n):
        store.patterns[f"p{i}"] = {
            "id": f"p{i}", "kind": "pattern", "accepted": True, "ascii": GRID, "features": {},
            "label": {"title": f"Ledge {i}", "purpose": "a hop", "mood": "calm", "mechanics": ["jump"],
                      "structure_type": "corridor", "difficulty": 2, "tags": ["ledge"]}}
    store.patterns["raw"] = {"id": "raw", "kind": "pattern", "accepted": True, "ascii": GRID,
                             "features": {}, "label": None}
    return store


def test_render_png_colours_cells():
    img = Image.open(io.BytesIO(render_png(GRID)))
    assert img.size == (5 * CELL_PX, 3 * CELL_PX)
    at = lambda x, y: img.getpixel((x * CELL_PX + 1, y * CELL_PX + 1))  # noqa: E731
    assert at(0, 0) == COLOURS["."] and at(4, 0) == COLOURS["X"] and at(1, 1) == COLOURS["S"]
    assert at(0, 2) == COLOURS["#"] and at(3, 2) == COLOURS["^"]


def test_describe_pending_adds_visual_and_embeds_it(tmp_path):
    store = _store(tmp_path)
    seen = []

    def fake(png, prompt):
        seen.append(prompt)
        assert png.startswith(b"\x89PNG")
        return {"visual": "A low step up to a ledge over a spike.", "tags": ["Step", "spike"],
                "label_ok": len(seen) != 2}

    rep = describe_pending(store, fake)
    assert (rep.described, rep.calls, rep.disagreed) == (3, 3, 1)
    assert "Ledge 0" in seen[0]  # the text label is shown to the vision model
    lab = store.patterns["p0"]["label"]
    assert lab["visual"].startswith("A low step") and lab["visual_tags"] == ["step", "spike"]
    assert store.patterns["raw"]["label"] is None  # unlabelled patterns are left alone
    assert "spike" in embed_text(store.patterns["p0"])
    again = describe_pending(store, fake)  # cached: no new calls
    assert (again.described, again.cached, again.calls) == (0, 3, 0)


def test_max_calls_and_bad_replies(tmp_path):
    store = _store(tmp_path)
    replies = iter([{"oops": 1}, {"visual": "Flat run.", "tags": []}])
    rep = describe_pending(store, lambda png, prompt: next(replies), max_calls=2)
    assert rep.stopped and rep.calls == 2 and rep.described == 1 and len(rep.errors) == 1


def test_missing_model_keeps_text_labels(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(vision, "model_available", lambda m: f"vision model {m} is not pulled")
    warnings = []
    rep = describe_pending(store, model="nope:1b", warn=warnings.append)
    assert rep.skipped and rep.described == 0 and len(warnings) == 1
    assert "visual" not in store.patterns["p0"]["label"]


def test_false_claims_negation_aware():
    facts = vision.grid_facts("S...X\n#####")
    assert (facts["hazards"], facts["enemies"], facts["start"]) == (0, 0, True)
    assert vision.false_claims("A flat run. There are no hazards or enemies.", facts) == []
    assert vision.false_claims("Spikes line the floor, but no enemies.", facts) == ["hazards"]
    assert vision.false_claims("Purple enemies patrol the ledge.", facts) == ["enemies"]
    assert vision.false_claims("Spikes on the floor.", vision.grid_facts(GRID)) == []


def test_false_claim_retried_then_dropped(tmp_path):
    store = _store(tmp_path, n=2)
    for rec in store.patterns.values():
        rec["ascii"] = "....X\n.S...\n#####"  # no hazard, no enemy
    prompts = []
    replies = iter([{"visual": "Spikes on the floor.", "tags": []},     # p0: wrong
                    {"visual": "A flat run to the exit.", "tags": []},  # p0 retry: fixed
                    {"visual": "An enemy waits.", "tags": []},           # p1: wrong
                    {"visual": "Lava below the run.", "tags": []}])      # p1 retry: still wrong

    def fake(png, prompt):
        prompts.append(prompt)
        return next(replies)

    rep = describe_pending(store, fake)
    assert '"hazards": 0' in prompts[0]  # the measured counts are in the prompt
    assert "mentioned hazards" in prompts[1]
    assert (rep.described, rep.retried, rep.calls, len(rep.errors)) == (1, 2, 4, 1)
    assert store.patterns["p0"]["label"]["visual"] == "A flat run to the exit."
    assert "visual" not in store.patterns["p1"]["label"]


def test_gemini_describer_request_pacing_and_backoff():
    from llm.providers.base import ProviderError

    calls, sleeps, now = [], [], [0.0]
    reply = {"candidates": [{"content": {"parts": [{"text": '{"visual": "A flat run.", "tags": []}'}]}}]}

    def post(url, headers, payload, timeout):
        calls.append((url, headers, payload))
        if len(calls) == 2:
            raise ProviderError("busy", status=503)
        return reply

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    d = vision.gemini_describer("gemini-x", api_key="k", rpm=12, backoff=10, post=post, sleep=sleep,
                                clock=lambda: now[0])
    assert d(b"png", "describe") == {"visual": "A flat run.", "tags": []}
    assert d(b"png", "again")["visual"] == "A flat run."
    url, headers, payload = calls[0]
    assert url.endswith("/models/gemini-x:generateContent") and "k" not in url
    assert headers["x-goog-api-key"] == "k"
    parts = payload["contents"][0]["parts"]
    assert parts[0]["text"] == "describe" and parts[1]["inline_data"]["mime_type"] == "image/png"
    assert "additionalProperties" not in payload["generationConfig"]["responseJsonSchema"]
    assert sleeps == [5.0, 10.0]  # 12 rpm pacing before call 2; 503 backoff; call 3 already paced
    assert d.model_name == "gemini:gemini-x"


def test_with_fallback_switches_after_repeated_failures():
    from llm.providers.base import ProviderError

    def primary(png, prompt):
        raise ProviderError("quota", status=429)
    primary.model_name = "gemini:g"

    def local(png, prompt):
        return {"visual": "local"}
    local.model_name = "ollama:q"

    warnings = []
    d = vision.with_fallback(primary, lambda: local, warnings.append, after=2)
    try:
        d(b"", "p")
        raise AssertionError("first failure must propagate")
    except ProviderError:
        pass
    assert d(b"", "p") == {"visual": "local"} and d.model_name == "ollama:q"
    assert d(b"", "p") == {"visual": "local"} and len(warnings) == 1 and d.state["switched"]
