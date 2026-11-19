"""Evaluation harness (offline): record/replay, metrics, resume, budget, 429, compare."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import run as ev_run
from eval.metrics import aggregate, compare_folders, load_records, p95, summarize, worst_requirements
from eval.providers import ReplayProvider
from llm import FakeProvider
from llm.providers.base import ProviderError

SUITE = [
    {"id": "alpha", "tags": ["single"], "prompt": "a minimal level"},
    {"id": "beta", "tags": ["multi"], "prompt": "meadow and dungeon", "expect": {"min_sections": 2}},
    {"id": "gamma", "tags": ["multi", "extra"], "prompt": "needs a castle", "expect": {"min_sections": 3}},
]


def _responses(load_fixture):
    return {
        "alpha": [load_fixture("valid_minimal.json")],
        "beta": [load_fixture("valid_two_sections.json")],
        "gamma": [
            load_fixture("completeness_reprompt/01_missing_castle.json"),
            load_fixture("completeness_reprompt/02_with_castle.json"),
        ],
    }


def _fake_for(responses):
    return lambda pid: FakeProvider(responses[pid])


def _run(out, responses, **kw):
    kw.setdefault("provider_name", "fake")
    kw.setdefault("model", "m1")
    kw.setdefault("log", lambda s: None)
    return ev_run.run_suite(SUITE, _fake_for(responses), out, **kw)


# ── the prompt suite ────────────────────────────────────────────────────────


def test_prompt_suite_shape_and_coverage():
    prompts = ev_run.load_prompts()
    assert len(prompts) >= 24
    ids = [p["id"] for p in prompts]
    assert len(set(ids)) == len(ids)
    tags = {t for p in prompts for t in p["tags"]}
    for needed in (
        "single", "multi", "horizontal", "vertical", "enemy", "pickup", "weapon", "interactive",
        "npc", "checkpoint", "key", "ability-has", "ability-lacks", "gravity", "size", "vague",
        "impossible", "long",
    ):
        assert needed in tags, needed
    assert all(p["prompt"].strip() for p in prompts)
    assert [p["id"] for p in ev_run.load_prompts(tags=["gravity"])] == ["gravity-low", "gravity-high"]
    assert len(ev_run.load_prompts(limit=3)) == 3


# ── record → replay ─────────────────────────────────────────────────────────


def test_record_then_replay_gives_identical_metrics(tmp_path, load_fixture):
    rec_dir = tmp_path / "rec"
    live = _run(tmp_path / "live", _responses(load_fixture), record_dir=rec_dir)
    assert (rec_dir / "meta.json").is_file()
    assert (rec_dir / "gamma" / "01.txt").is_file()  # keyed by prompt id and call index

    replayed = ev_run.run_suite(
        SUITE,
        lambda pid: ReplayProvider(rec_dir, pid),
        tmp_path / "replay",
        provider_name="fake",
        model="m1",
        log=lambda s: None,
    )
    key = lambda recs: [  # noqa: E731
        (r["id"], r["verified"], r["fidelity_score"], r["llm_calls"], r["fidelity_counts"]) for r in recs
    ]
    assert key(live) == key(replayed)
    assert all(r["verified"] for r in live)
    assert [r["llm_calls"] for r in live] == [1, 1, 2]
    assert live[2]["completeness_reprompts"] == 1
    for name in ("results.jsonl", "summary.json", "report.md"):
        assert (tmp_path / "replay" / name).is_file()
    assert "## Worst requirements" in (tmp_path / "replay" / "report.md").read_text()


def test_cli_replay_uses_meta_and_prompt_file(tmp_path, load_fixture):
    resp = _responses(load_fixture)
    rec_dir = tmp_path / "rec"
    _run(tmp_path / "live", resp, record_dir=rec_dir)
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"prompts": SUITE}))
    out = tmp_path / "cli"
    code = ev_run.main(["--replay", str(rec_dir), "--prompts", str(suite), "--out", str(out), "--limit", "2"])
    assert code == 0
    recs = load_records(out)
    assert [r["id"] for r in recs] == ["alpha", "beta"]
    assert recs[0]["provider"] == "fake" and recs[0]["model"] == "m1"  # from meta.json


def test_replay_missing_response_is_a_failure_not_a_crash(tmp_path, load_fixture):
    recs = ev_run.run_suite(
        SUITE[:1],
        lambda pid: ReplayProvider(tmp_path / "nothing", pid),
        tmp_path / "out",
        provider_name="fake",
        model=None,
        log=lambda s: None,
    )
    assert recs[0]["failure"]["type"] == "replay_missing"
    assert recs[0]["verified"] is False


# ── metrics ─────────────────────────────────────────────────────────────────


def test_aggregation(tmp_path, load_fixture):
    recs = _run(tmp_path / "o", _responses(load_fixture))
    s = aggregate(recs)
    assert s["prompts"] == 3 and s["verified"] == 3 and s["playable_share"] == 1.0
    assert s["llm_calls_total"] == 4
    assert s["completeness_reprompts_mean"] == pytest.approx(1 / 3)
    scores = [r["fidelity_score"] for r in recs if r["fidelity_score"] is not None]
    assert s["fidelity_mean"] == pytest.approx(sum(scores) / len(scores))
    assert sum(s["status_counts"].values()) == sum(sum(r["fidelity_counts"].values()) for r in recs)
    assert s["wall_p95_s"] >= s["wall_mean_s"] * 0 and s["wall_p95_s"] == max(r["wall_time_s"] for r in recs)
    assert list(summarize(recs)) == ["fake/m1"]


def test_aggregation_synthetic_failures_and_worst_requirements():
    item = lambda text, status: {"id": "R", "text": text, "category": "entity", "status": status}  # noqa: E731
    recs = [
        {"id": "a", "verified": True, "fidelity_score": 1.0, "wall_time_s": 1.0, "llm_calls": 1,
         "fidelity_counts": {"satisfied": 1}, "fidelity_items": [item("3 coins", "satisfied")]},
        {"id": "b", "verified": True, "fidelity_score": 0.5, "wall_time_s": 3.0, "llm_calls": 2,
         "fidelity_counts": {"missing": 1}, "fidelity_items": [item("3  Coins", "missing")]},
        {"id": "c", "verified": False, "failure": {"type": "MaxRetriesExceeded"}, "wall_time_s": 2.0,
         "llm_calls": 3, "fidelity_score": None},
        {"id": "d", "verified": False, "failure": {"type": "quota"}, "wall_time_s": 0.1, "llm_calls": 1},
    ]
    s = aggregate(recs)
    assert s["playable_share"] == 0.5
    assert s["failures"] == {"MaxRetriesExceeded": 1, "quota": 1}
    assert s["fidelity_mean"] == 0.75 and s["fidelity_scored"] == 2
    assert s["llm_calls_total"] == 7
    assert p95([1, 2, 3, 4, 100]) == 100 and p95([]) is None
    worst = worst_requirements(recs)
    assert worst[0]["text"] == "3 coins" and worst[0]["seen"] == 2 and worst[0]["missing"] == 1


def test_expectation_failures_are_recorded(tmp_path, load_fixture):
    recs = _run(tmp_path / "o", _responses(load_fixture))
    by = {r["id"]: r for r in recs}
    assert by["alpha"]["expect_failures"] == []
    assert by["beta"]["expect_failures"] == []
    assert by["gamma"]["expect_failures"] == []  # the re-prompt added the third section
    suite = [{"id": "alpha", "tags": [], "prompt": "x", "expect": {"min_sections": 5, "entity_kinds": ["NPC"]}}]
    recs = ev_run.run_suite(
        suite, _fake_for(_responses(load_fixture)), tmp_path / "o2",
        provider_name="fake", model=None, log=lambda s: None,
    )
    assert len(recs[0]["expect_failures"]) == 2


# ── resume, budget, quota ───────────────────────────────────────────────────


def test_resume_skips_prompts_with_results(tmp_path, load_fixture):
    resp = _responses(load_fixture)
    out = tmp_path / "o"
    ev_run.run_suite(SUITE[:1], _fake_for(resp), out, provider_name="fake", model=None, log=lambda s: None)
    asked = []

    def factory(pid):
        asked.append(pid)
        return FakeProvider(resp[pid])

    recs = ev_run.run_suite(SUITE, factory, out, provider_name="fake", model=None, log=lambda s: None)
    assert asked == ["beta", "gamma"]
    assert [r["id"] for r in recs] == ["alpha", "beta", "gamma"]
    assert len((out / "results.jsonl").read_text().splitlines()) == 3


def test_max_calls_stops_before_the_budget_is_spent(tmp_path, load_fixture):
    resp = _responses(load_fixture)
    out = tmp_path / "o"
    # alpha (1 call) fits; beta would be the 2nd call -> budget of 1 stops before it.
    recs = _run(out, resp, max_calls=1)
    assert [r["id"] for r in recs] == ["alpha"]

    # gamma needs 2 calls: with room for only 1 it is abandoned, not recorded.
    out2 = tmp_path / "o2"
    recs = ev_run.run_suite(
        SUITE[2:], _fake_for(resp), out2, provider_name="fake", model=None, max_calls=1, log=lambda s: None
    )
    assert recs == []

    # resuming with a bigger budget finishes the rest and keeps alpha.
    recs = _run(out, resp, max_calls=10)
    assert [r["id"] for r in recs] == ["alpha", "beta", "gamma"]


class _QuotaProvider:
    name = "quota"

    def complete_json(self, system, messages, schema):
        raise ProviderError("LLM API HTTP error 429: RESOURCE_EXHAUSTED", status=429)


def test_429_is_a_quota_failure_and_is_retried_on_resume(tmp_path, load_fixture):
    resp = _responses(load_fixture)
    out = tmp_path / "o"
    recs = ev_run.run_suite(
        SUITE, lambda pid: _QuotaProvider() if pid == "beta" else FakeProvider(resp[pid]),
        out, provider_name="fake", model=None, log=lambda s: None,
    )
    assert [r["id"] for r in recs] == ["alpha", "beta"]  # stopped after the first 429
    beta = recs[1]
    assert beta["failure"]["type"] == "quota" and beta["verified"] is False
    assert beta["llm_calls"] == 1
    assert aggregate(recs)["failures"] == {"quota": 1}

    # --keep-going continues past it
    out_k = tmp_path / "k"
    recs = ev_run.run_suite(
        SUITE, lambda pid: _QuotaProvider() if pid == "beta" else FakeProvider(resp[pid]),
        out_k, provider_name="fake", model=None, keep_going=True, log=lambda s: None,
    )
    assert [r["id"] for r in recs] == ["alpha", "beta", "gamma"]

    # resuming re-runs the quota failure only
    recs = ev_run.run_suite(SUITE, _fake_for(resp), out, provider_name="fake", model=None, log=lambda s: None)
    assert [r["id"] for r in recs] == ["alpha", "beta", "gamma"] or {r["id"] for r in recs} == {"alpha", "beta", "gamma"}
    assert all(r["verified"] for r in recs)


def test_max_retries_exceeded_is_classified(tmp_path):
    suite = [{"id": "bad", "tags": [], "prompt": "x"}]
    recs = ev_run.run_suite(
        suite, lambda pid: FakeProvider(["not json"] * 3), tmp_path / "o",
        provider_name="fake", model=None, log=lambda s: None,
    )
    assert recs[0]["failure"]["type"] == "MaxRetriesExceeded"
    assert recs[0]["llm_calls"] == 3 and recs[0]["llm_attempts"] == 3


# ── compare ─────────────────────────────────────────────────────────────────


def test_compare_reports_deltas_and_regressions(tmp_path, load_fixture):
    resp = _responses(load_fixture)
    a, b = tmp_path / "a", tmp_path / "b"
    _run(a, resp)
    resp_b = dict(resp)
    resp_b["beta"] = ["not json"] * 3  # B can no longer build beta
    _run(b, resp_b)
    text = compare_folders(a, b)
    assert "| playable share |" in text
    assert "REGRESSED" in text
    line = next(l for l in text.splitlines() if l.startswith("| beta "))
    assert "MaxRetriesExceeded" in line
    assert ev_run.main(["--compare", str(a), str(a)]) == 0


def test_records_the_model_that_answered_when_none_was_requested(tmp_path, load_fixture):
    resp = _responses(load_fixture)

    def factory(pid):
        fake = FakeProvider(resp[pid])
        fake.model_used = "gemini-3.1-flash-lite"  # e.g. Gemini fell back after a 503
        return fake

    recs = ev_run.run_suite(SUITE[:1], factory, tmp_path / "o", provider_name="gemini", model=None,
                            record_dir=tmp_path / "rec", log=lambda s: None)
    assert recs[0]["model"] == "gemini-3.1-flash-lite"
    recs = ev_run.run_suite(SUITE[1:2], factory, tmp_path / "o2", provider_name="gemini",
                            model="gemini-3.8-flash", log=lambda s: None)
    assert recs[0]["model"] == "gemini-3.8-flash"  # an explicit model is kept as requested


# ── 503 backoff and --fallback ──────────────────────────────────────────────


class _FlakyProvider:
    """Raises 503 *fails* times, then answers from *responses*."""

    def __init__(self, fails, responses, name="gemini", model="gemini-3.1-flash-lite"):
        self.fails, self.name, self.model = fails, name, model
        self.model_used = None
        self._fake = FakeProvider(responses)
        self.calls = 0

    def complete_json(self, system, messages, schema):
        self.calls += 1
        if self.fails > 0:
            self.fails -= 1
            raise ProviderError("HTTP 503: UNAVAILABLE, high demand", status=503)
        self.model_used = self.model
        return self._fake.complete_json(system, messages, schema)


def test_backoff_retries_503_then_answers(tmp_path, load_fixture):
    from eval.providers import BackoffProvider

    waits = []
    flaky = _FlakyProvider(2, [load_fixture("valid_minimal.json")])
    bp = BackoffProvider(flaky, [20, 60, 120], sleep=waits.append)
    recs = ev_run.run_suite(SUITE[:1], lambda pid: bp, tmp_path / "o", provider_name="gemini",
                            model="gemini-3.1-flash-lite", backoff=bp, log=lambda s: None)
    r = recs[0]
    assert r["verified"] and waits == [20, 60]
    assert r["provider_retries"] == 2 and r["llm_calls"] == 1
    assert r["answered_by"] == ["gemini/gemini-3.1-flash-lite"]
    assert r["wall_time_s"] < 20  # the waits are not counted


def test_backoff_gives_up_after_the_last_delay_and_ignores_other_errors():
    from eval.providers import BackoffProvider

    waits = []
    bp = BackoffProvider(_FlakyProvider(9, []), [1, 2, 3], sleep=waits.append)
    with pytest.raises(ProviderError):
        bp.complete_json("s", [], {})
    assert waits == [1, 2, 3] and bp.retries == 3
    bp = BackoffProvider(_QuotaProvider(), [1, 2, 3], sleep=waits.append)
    with pytest.raises(ProviderError):
        bp.complete_json("s", [], {})
    assert bp.retries == 0  # a 429 is not retried


def test_fallback_flag_wraps_primary_with_backoff_and_records_the_answerer(tmp_path, load_fixture):
    from llm.providers.fallback import FallbackProvider

    waits = []
    gemini = _FlakyProvider(99, [])  # never answers
    local = FakeProvider([load_fixture("valid_minimal.json")])
    local.name, local.model = "ollama", "qwen2.5:14b"
    seen = {}

    def with_fallback(name, model=None):
        seen["args"] = (name, model)
        return FallbackProvider([gemini, local])

    shared, bp = ev_run.build_live_provider("gemini", "gemini-3.1-flash-lite", fallback=True,
                                            delays=[20, 60, 120], make_with_fallback=with_fallback,
                                            sleep=waits.append, log=lambda s: None)
    assert seen["args"] == ("gemini", "gemini-3.1-flash-lite")
    recs = ev_run.run_suite(SUITE[:1], lambda pid: shared, tmp_path / "o", provider_name="gemini",
                            model="gemini-3.1-flash-lite", backoff=bp, record_dir=tmp_path / "rec",
                            log=lambda s: None)
    r = recs[0]
    assert r["verified"] and waits == [20, 60, 120] and gemini.calls == 4
    assert r["answered_by"] == ["ollama/qwen2.5:14b"] and r["provider_retries"] == 3
    assert r["fallback_reasons"] == ["HTTP 503"]

    # without --fallback: plain make_provider, backoff around it; --backoff 0 disables it
    local_fake = FakeProvider([load_fixture("valid_minimal.json")])
    plain, bp = ev_run.build_live_provider("gemini", None, make=lambda n, model=None: local_fake,
                                           delays=ev_run.parse_backoff("0"))
    assert plain is local_fake and bp is None
    assert ev_run.parse_backoff("20,60,120") == [20.0, 60.0, 120.0]


def test_gemini_fallback_reasons_order_dedup_reset_and_exception():
    from llm.providers.gemini import GeminiProvider

    class _MockGemini(GeminiProvider):
        def __init__(self, script):
            super().__init__(
                api_key="test",
                fallback_models=("m1", "m2", "m3", "m4"),
            )
            self._script = list(script)

        def _post_with_retry(self, url, headers, payload):
            nxt = self._script.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            return {"candidates": [{"content": {"parts": [{"text": nxt}]}}]}

    # 1. Deduplicates while preserving order across 429, 503, duplicate 429, 404, timeout
    mg = _MockGemini([
        ProviderError("rate", status=429),
        ProviderError("busy", status=503),
        ProviderError("rate2", status=429),
        ProviderError("gone", status=404),
        "{}",
    ])
    assert mg.complete_json("s", [], {}) == "{}"
    assert mg.fallback_reasons == ["HTTP 429", "HTTP 503", "HTTP 404"]

    # 2. Resets per request on success
    mg._script = ["{}"]
    assert mg.complete_json("s", [], {}) == "{}"
    assert mg.fallback_reasons == []

    # 3. Records timeout and keeps reasons even when a later model raises
    mg._script = [
        ProviderError("slow", timed_out=True),
        ProviderError("bad", status=400),
    ]
    with pytest.raises(ProviderError):
        mg.complete_json("s", [], {})
    assert mg.fallback_reasons == ["timeout"]

    # 4. Resets when API key is missing on a subsequent call
    mg._api_key = None
    with pytest.raises(ProviderError):
        mg.complete_json("s", [], {})
    assert mg.fallback_reasons == []


def test_fallback_provider_reasons_and_wrappers(tmp_path, load_fixture):
    from eval.metrics import aggregate, render_report
    from eval.providers import CallBudget, CountingProvider, RecordingProvider
    from llm.providers.fallback import FallbackProvider

    class _StubProvider:
        def __init__(self, name, model, outcome, inner_reasons=()):
            self.name = name
            self.model = model
            self.outcome = outcome
            self.fallback_reasons = list(inner_reasons)

        def complete_json(self, system, messages, schema):
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return self.outcome

    ok_json = load_fixture("valid_minimal.json")

    # Merges inner reasons + switch reason, deduplicated in order, across all reason types
    cases = [
        (ProviderError("no key", missing_key=True), (), ["no API key"]),
        (ProviderError("net"), (), ["connection error"]),
        (ProviderError("slow", timed_out=True), (), ["timeout"]),
        (ProviderError("gone", status=404), ("HTTP 429", "HTTP 404"), ["HTTP 429", "HTTP 404"]),
    ]
    for err, inner_r, expected in cases:
        fp = FallbackProvider([_StubProvider("gemini", "g1", err, inner_r), _StubProvider("ollama", "o1", ok_json)])
        assert fp.complete_json("s", [], {}) == ok_json
        assert fp.fallback_reasons == expected
        # Sticky second call directly on ollama resets fallback_reasons
        assert fp.complete_json("s", [], {}) == ok_json
        assert fp.fallback_reasons == []

    # Non-fallback 400 and terminal last-provider error are not added as fallback reasons,
    # while earlier fallback reasons ARE preserved on exception
    fp_400 = FallbackProvider([
        _StubProvider("gemini", "g1", ProviderError("bad", status=400), inner_reasons=("HTTP 429",)),
        _StubProvider("ollama", "o1", ok_json),
    ])
    with pytest.raises(ProviderError):
        fp_400.complete_json("s", [], {})
    assert fp_400.fallback_reasons == ["HTTP 429"]

    fp_all_fail = FallbackProvider([
        _StubProvider("gemini", "g1", ProviderError("quota", status=429)),
        _StubProvider("ollama", "o1", ProviderError("refused")),
    ])
    rp = RecordingProvider(fp_all_fail, tmp_path / "rec", "p_fail")
    cp = CountingProvider(rp, CallBudget())
    with pytest.raises(ProviderError):
        cp.complete_json("s", [], {})
    assert fp_all_fail.fallback_reasons == ["HTTP 429"]
    assert rp.fallback_reasons == ["HTTP 429"]
    assert cp.fallback_reasons == ["HTTP 429"]

    # CountingProvider deduplicates across multiple calls for one prompt
    p1 = FallbackProvider([
        _StubProvider("gemini", "g1", ProviderError("quota", status=429), inner_reasons=("HTTP 503",)),
        _StubProvider("ollama", "o1", ok_json),
    ])
    p2 = FallbackProvider([
        _StubProvider("gemini", "g1", ProviderError("slow", timed_out=True), inner_reasons=("HTTP 429",)),
        _StubProvider("ollama", "o1", ok_json),
    ])
    cp2 = CountingProvider(RecordingProvider(p1, tmp_path / "rec", "p_ok"), CallBudget())
    cp2.complete_json("s", [], {})
    cp2._inner = RecordingProvider(p2, tmp_path / "rec", "p_ok2")
    cp2.complete_json("s", [], {})
    assert cp2.fallback_reasons == ["HTTP 503", "HTTP 429", "timeout"]

    # aggregate & render_report without reasons omit the suffix; with reasons include counts
    no_fb = [{"id": "a", "verified": True, "fidelity_score": 1.0, "wall_time_s": 0.1, "llm_calls": 1}]
    assert aggregate(no_fb)["fallback_reasons"] == {}
    assert "fallback reasons:" not in render_report(no_fb)


def test_run_suite_tracks_fallback_reasons_across_reprompt_and_failure(tmp_path, load_fixture):
    from eval.metrics import aggregate, render_report
    from llm.providers.fallback import FallbackProvider

    class _ScriptedChain:
        name = "gemini"
        model = "gemini-3.8-flash"

        def __init__(self, steps):
            self._steps = list(steps)
            self.fallback_reasons = []

        def complete_json(self, system, messages, schema):
            reasons, outcome = self._steps.pop(0)
            self.fallback_reasons = list(reasons)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    alpha_resp = load_fixture("valid_minimal.json")
    gamma_1 = load_fixture("completeness_reprompt/01_missing_castle.json")
    gamma_2 = load_fixture("completeness_reprompt/02_with_castle.json")

    def factory(pid):
        if pid == "alpha":
            return _ScriptedChain([([], alpha_resp)])
        if pid == "beta":
            return FallbackProvider([
                _ScriptedChain([(["HTTP 429"], ProviderError("busy", status=503))]),
                _ScriptedChain([([], ProviderError("ollama down"))]),
            ])
        # gamma: call 1 returns gamma_1 after 429+503; call 2 runs completeness reprompt with timeout
        return _ScriptedChain([
            (["HTTP 429", "HTTP 503"], gamma_1),
            (["HTTP 503", "timeout"], gamma_2),
        ])

    out = tmp_path / "eval_out"
    rec_dir = tmp_path / "rec_dir"
    recs = ev_run.run_suite(
        SUITE, factory, out, provider_name="gemini", model="gemini-3.8-flash",
        record_dir=rec_dir, keep_going=True, log=lambda s: None,
    )
    by_id = {r["id"]: r for r in recs}
    assert by_id["alpha"]["fallback_reasons"] == []
    assert by_id["beta"]["failure"]["type"] == "ProviderError"
    assert by_id["beta"]["fallback_reasons"] == ["HTTP 429", "HTTP 503"]
    assert by_id["gamma"]["verified"] is True
    assert by_id["gamma"]["fallback_reasons"] == ["HTTP 429", "HTTP 503", "timeout"]

    agg = aggregate(recs)
    assert agg["fallback_reasons"] == {"HTTP 429": 2, "HTTP 503": 2, "timeout": 1}
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "fallback reasons: HTTP 429 x2, HTTP 503 x2, timeout x1" in report
    assert "fallback reasons:" in render_report(recs)


def test_each_prompt_starts_the_fallback_chain_on_the_primary(load_fixture):
    from llm.providers.fallback import FallbackProvider

    gemini = _FlakyProvider(1, [load_fixture("valid_minimal.json")])  # 503 once, then answers
    local = FakeProvider([load_fixture("valid_minimal.json")])
    local.name = "ollama"
    base = ev_run.per_prompt(FallbackProvider([gemini, local]))
    first = base("a")
    first.complete_json("s", [], {})
    assert first.name == "ollama"  # switched for this prompt
    second = base("b")
    second.complete_json("s", [], {})
    assert second.name == "gemini"  # the next prompt tries Gemini again
