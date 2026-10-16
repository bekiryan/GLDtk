"""Job progress and cancel (v3 Part 14): the registry, provider messages, and the routes.

The route tests run a request in one thread and poll ``/progress`` / post
``/cancel`` from the test thread, as the sidebar does.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from api import jobs
from api.jobs import Cancelled, JobRegistry
from llm import FakeProvider
from llm.providers import FallbackProvider, ProviderError, base, gemini
from llm.providers.ollama import OllamaProvider
from tests.conftest import fixture_text

MESSAGES = [{"role": "user", "content": "hi"}]
SCHEMA = {"type": "object"}
OK = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}


def messages_of(job_id: str):
    return [e["message"] for e in jobs.registry.progress(job_id)["events"]]


# ─────────────────────────────────────────────────────────────────────────────
# Registry and reporter
# ─────────────────────────────────────────────────────────────────────────────


def test_unknown_job_is_done():
    p = jobs.registry.progress("no-such-job")
    assert p == {"stage": None, "events": [], "elapsed_s": 0.0, "done": True, "cancelled": False}
    body = TestClient(server.app).get("/progress", params={"job": "no-such-job"}).json()
    assert body["done"] is True and body["events"] == []


def test_reporting_without_a_job_is_a_no_op():
    jobs.report("llm", "nobody listens")
    jobs.checkpoint()
    t0 = time.monotonic()
    jobs.sleep(0)
    assert time.monotonic() - t0 < 0.5


def test_events_are_capped_and_finished_jobs_expire(monkeypatch):
    reg = JobRegistry(keep_seconds=0.05)
    j = reg.start("a")
    for i in range(80):
        reg.add_event(j, "llm", f"m{i}")
    p = reg.progress("a")
    assert len(p["events"]) == jobs.MAX_EVENTS and p["events"][-1]["message"] == "m79"
    assert p["stage"] == "llm" and not p["done"]
    reg.finish(j)
    assert reg.progress("a")["done"]
    time.sleep(0.1)
    assert reg.get("a") is None


def test_job_context_records_start_and_finish():
    jid = uuid.uuid4().hex
    with jobs.job(jid):
        jobs.report("layout", "Laying out the level")
    p = jobs.registry.progress(jid)
    assert p["done"] and p["stage"] == "done"
    assert [e["stage"] for e in p["events"]] == ["layout", "done"]


def test_cancel_before_the_request_arrives_still_cancels():
    jid = uuid.uuid4().hex
    assert jobs.registry.cancel(jid) is False  # not running yet
    with pytest.raises(Cancelled):
        with jobs.job(jid):
            jobs.report("start", "Generating the level")
    assert jobs.registry.progress(jid)["cancelled"]


def test_cancel_interrupts_sleep():
    jid = uuid.uuid4().hex
    threading.Timer(0.2, jobs.registry.cancel, args=(jid,)).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        with jobs.job(jid):
            jobs.sleep(30)
    assert time.monotonic() - t0 < 2


# ─────────────────────────────────────────────────────────────────────────────
# Provider messages
# ─────────────────────────────────────────────────────────────────────────────


def test_gemini_503_retry_message(monkeypatch):
    calls = {"n": 0}

    def flaky(url, headers, payload, timeout_seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("overloaded", status=503)
        return OK

    monkeypatch.setattr(base, "http_json_post", flaky)
    monkeypatch.setattr(jobs, "sleep", lambda s: None)
    jid = uuid.uuid4().hex
    with jobs.job(jid):
        gemini.GeminiProvider(api_key="K").complete_json("s", MESSAGES, SCHEMA)
    assert "Gemini busy (503), retrying in 5 s (1/2)" in messages_of(jid)


def test_gemini_model_fallback_message(monkeypatch):
    def quota_primary(url, headers, payload, timeout_seconds):
        if f"/models/{gemini.DEFAULT_MODEL}:" in url:
            raise ProviderError("quota", status=429)
        return OK

    monkeypatch.setattr(base, "http_json_post", quota_primary)
    jid = uuid.uuid4().hex
    with jobs.job(jid):
        p = gemini.GeminiProvider(api_key="K", fallback_models=gemini.FALLBACK_MODELS)
        p.complete_json("s", MESSAGES, SCHEMA)
    assert (f"{gemini.DEFAULT_MODEL} out of quota, trying {gemini.FALLBACK_MODELS[0]}"
            in messages_of(jid))


def test_local_fallback_message(monkeypatch):
    def gemini_down(url, headers, payload, timeout_seconds):
        if "generativelanguage" in url:
            raise ProviderError("quota", status=429)
        return {"message": {"content": "{}"}}

    monkeypatch.setattr(base, "http_json_post", gemini_down)
    jid = uuid.uuid4().hex
    with jobs.job(jid):
        fb = FallbackProvider([gemini.GeminiProvider(api_key="K"),
                               OllamaProvider(model="qwen2.5:14b")])
        assert fb.complete_json("s", MESSAGES, SCHEMA) == "{}"
    assert ("Switched to local model qwen2.5:14b (this can take a few minutes)"
            in messages_of(jid))


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


class SlowProvider(FakeProvider):
    """A FakeProvider whose call takes *delay* seconds, or blocks until *gate* is set."""

    def __init__(self, responses, delay: float = 0.0, gate: threading.Event | None = None):
        super().__init__(responses)
        self.delay = delay
        self.gate = gate
        self.entered = threading.Event()

    def complete_json(self, system, messages, schema):
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(10)
        time.sleep(self.delay)
        return super().complete_json(system, messages, schema)


def post_in_thread(path: str, payload: dict):
    box: dict = {}

    def run():
        box["resp"] = TestClient(server.app).post(path, json=payload)

    t = threading.Thread(target=run)
    t.start()
    return t, box


def wait_for(cond, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_progress_is_served_while_generate_runs(monkeypatch, tmp_path):
    slow = SlowProvider([fixture_text("valid_two_sections.json")], delay=0.6)
    monkeypatch.setattr(server, "provider_factory", lambda req: slow)
    jid = uuid.uuid4().hex
    out = tmp_path / "level.ldtk"
    t, box = post_in_thread("/generate", {"description": "meadow then dungeon",
                                          "output_path": str(out), "job_id": jid})
    client = TestClient(server.app)
    seen = []
    while t.is_alive():
        body = client.get("/progress", params={"job": jid}).json()
        if body["events"]:
            seen.append(body)
        time.sleep(0.05)
    t.join()
    assert box["resp"].json()["success"]
    mid = [b for b in seen if not b["done"]]
    assert mid, "no progress was served while the request ran"
    assert any(e["message"] == "Asking fake for the level spec" for b in mid for e in b["events"])
    final = client.get("/progress", params={"job": jid}).json()
    stages = [e["stage"] for e in final["events"]]
    for s in ("start", "llm", "validate", "layout", "verify", "densify", "aesthetic", "export",
              "done"):
        assert s in stages, (s, stages)
    assert final["done"] and final["elapsed_s"] >= 0.5


def test_cancel_during_the_llm_call_writes_nothing(monkeypatch, tmp_path):
    gate = threading.Event()
    slow = SlowProvider([fixture_text("valid_two_sections.json")], gate=gate)
    monkeypatch.setattr(server, "provider_factory", lambda req: slow)
    jid = uuid.uuid4().hex
    out = tmp_path / "gen" / "level.ldtk"
    t, box = post_in_thread("/generate", {"description": "meadow then dungeon",
                                          "output_path": str(out), "job_id": jid})
    assert slow.entered.wait(10)
    client = TestClient(server.app)
    assert client.post("/cancel", json={"job_id": jid}).json() == {"job_id": jid, "running": True}
    gate.set()  # the in-flight call returns; the check right after it stops the job
    t.join(10)
    body = box["resp"].json()
    assert body["success"] is False and body["error"] == "Cancelled" and body["cancelled"] is True
    assert not out.parent.exists()  # no file, no assets, no history snapshot
    prog = client.get("/progress", params={"job": jid}).json()
    assert prog["done"] and prog["cancelled"] and prog["stage"] == "cancelled"


def test_cancel_during_a_503_backoff_returns_fast(monkeypatch, tmp_path):
    def busy(url, headers, payload, timeout_seconds):
        raise ProviderError("overloaded", status=503)

    monkeypatch.setattr(base, "http_json_post", busy)
    monkeypatch.setattr(server, "provider_factory",
                        lambda req: gemini.GeminiProvider(api_key="K", backoff_seconds=5))
    jid = uuid.uuid4().hex
    out = tmp_path / "level.ldtk"
    t, box = post_in_thread("/generate", {"description": "x", "output_path": str(out),
                                          "job_id": jid})
    assert wait_for(lambda: any("retrying" in m for m in messages_of(jid)))
    t0 = time.monotonic()
    TestClient(server.app).post("/cancel", json={"job_id": jid})
    t.join(10)
    assert time.monotonic() - t0 < 2  # not the 5 s + 10 s of backoff
    assert box["resp"].json()["cancelled"] is True
    assert not out.exists()


def _base_level(tmp_path: Path) -> Path:
    out = tmp_path / "level.ldtk"
    fx = fixture_text("valid_two_sections.json")
    old = server.provider_factory
    server.provider_factory = lambda req: FakeProvider([fx])
    try:
        body = TestClient(server.app).post(
            "/generate", json={"description": "meadow then dungeon", "output_path": str(out),
                               "densify": False}).json()
    finally:
        server.provider_factory = old
    assert body["success"], body.get("error")
    return out


def test_cancelled_region_edit_leaves_the_file_and_history_alone(monkeypatch, tmp_path):
    level = _base_level(tmp_path)
    before = level.read_bytes()
    history = tmp_path / ".gldtk_history"
    snaps_before = sorted(p.name for p in history.rglob("*")) if history.exists() else []
    gate = threading.Event()
    slow = SlowProvider(["{}"], gate=gate)
    monkeypatch.setattr(server, "provider_factory", lambda req: slow)
    jid = uuid.uuid4().hex
    t, box = post_in_thread("/edit/region", {
        "path": str(level), "rect": {"col": 10, "row": 10, "w": 11, "h": 10},
        "prompt": "a lava pit", "job_id": jid})
    assert slow.entered.wait(10)
    TestClient(server.app).post("/cancel", json={"job_id": jid})
    gate.set()
    t.join(10)
    body = box["resp"].json()
    assert body["cancelled"] is True and body["error"] == "Cancelled"
    assert level.read_bytes() == before
    snaps_after = sorted(p.name for p in history.rglob("*")) if history.exists() else []
    assert snaps_after == snaps_before
    msgs = messages_of(jid)
    assert msgs[0] == "Reading the level for a region edit"
    assert any(m.startswith("Found ") and "seam" in m for m in msgs)


def test_requests_without_job_id_still_work(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "provider_factory",
                        lambda req: FakeProvider([fixture_text("valid_minimal.json")]))
    body = TestClient(server.app).post(
        "/generate", json={"description": "tiny", "output_path": str(tmp_path / "l.ldtk")}).json()
    assert body["success"] and body["cancelled"] is False
