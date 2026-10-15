# Part 14: Live progress and Cancel (Python + Haxe)

Context: [../PLAN.md](../PLAN.md). During the first evaluation, single Gemini calls took up to
4 minutes: the provider retries 503 errors with backoff, then falls back to flash-lite and then
to the local model. All that time the sidebar only says "generating…", and there is no way to stop.

## A. Python (in a worktree)
- **Jobs.**
  - Every long request (`/generate`, `/edit/region`, `/edit/section`, `/edit/entities`,
    `/edit/locate`) accepts an optional `job_id` (a client-made uuid).
  - A small thread-safe registry (new `api/jobs.py`) keeps, per job: the current `stage`, a list
    of `events` (`{t, stage, message}`, newest last, capped at about 50), `cancelled`, and
    `started_at`.
  - Entries are removed about 10 minutes after they finish.
- **Endpoints.**
  - `GET /progress?job=<id>` returns `{stage, events, elapsed_s, done}`, and unknown ids give
    `done: true`.
  - `POST /cancel {job_id}` sets the flag.
  - The FastAPI routes are sync, so they run in the threadpool, and polling works while a
    request runs. Verify that with a test that polls from another thread.
- **Stages and messages** (short, user-facing):
  - LLM call started (provider/model);
  - "Gemini busy (503), retrying in 5 s (1/2)";
  - "gemini-3.8-flash out of quota, trying gemini-3.1-flash-lite";
  - "switched to local model qwen2.5:14b (this can take a few minutes)";
  - spec validation and patches;
  - completeness re-prompt;
  - layout;
  - densify;
  - grid verification and repair (round N);
  - aesthetic;
  - export;
  - for edits: seams, region, stitch and verify.
- **How events are reported.**
  - Use a context-local reporter (`contextvars`), so deep code can call `report(stage, msg)`
    without changing signatures. The providers (`llm/providers/gemini.py` retry and fallback,
    `fallback.py`) call it.
  - Keep providers usable without a job; the reporter is then a no-op.
- **Cancel.**
  - Check the flag between stages and before every LLM call, and raise a `Cancelled` error.
  - An in-flight HTTP call cannot be interrupted, so check right after it returns, and skip
    remaining retries and backoff sleeps. Make the sleeps interruptible, for example with an
    `Event.wait`.
  - The response is `{success:false, error:"Cancelled", cancelled:true}`, and **nothing is
    written**: no file, and no snapshot left behind.
- **Tests (offline):**
  - Progress events appear for a fake generate. Use a FakeProvider that sleeps, and poll from a
    thread.
  - Cancel during the LLM call writes nothing and returns `cancelled`.
  - Cancel during a 503 backoff returns fast: monkeypatch `http_json_post` to 503.
  - Messages for the 503 retry, the model fallback and the local fallback.
  - Unknown job ids are handled.
  - All of `pytest` passes.
- **You own:** `api/jobs.py`, `server.py` and `api/models.py` (the job and cancel bits),
  `llm/providers/*` (the reporting calls only), `pipeline/level.py` and `edit/*.py` (the
  `report()` and cancel-check calls only), and tests.

## B. Haxe (`ldtk-src`)
- **While a generate or edit runs:**
  - The sidebar makes a uuid `job_id`, sends it, and polls `/progress` every 1 s.
  - It shows the latest message under the status, with the elapsed time ("Gemini busy (503),
    retrying… 0:42").
  - A small expandable log lists all events.
- **Cancel.**
  - The Generate, Apply and Edit buttons turn into **Cancel** while busy, and pressing it calls
    `/cancel`.
  - When the response arrives with `cancelled`, show "Cancelled. Nothing was changed." The level
    is not reloaded.
  - Esc in the prompt field also cancels.
- **When the request ends:** stop polling on success, error, cancel or project close; make sure
  no timers leak.
- **Testing:** Playwright against a fake server whose provider sleeps and reports events (reuse
  the `sw/slow_server.py` pattern from the session scratchpad). Check that:
  - the messages update;
  - Cancel works and the file is unchanged (compare bytes);
  - a normal generate still reloads.

  Take screenshots. Rebuild with `renderer.debug.hxml`; there must be no errors.
- **Commits:** worktree `v3 Part 14a: job progress and cancel API`; `ldtk-src`
  `v3 Part 14b: live progress and cancel in the sidebar`.

## Shared rules
- End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run `git reset --hard feat/v2` in the worktree first.
- Do not push, do not edit `docs/v2/PROGRESS.md`, and never call a real LLM.
- Port 8765 is yours for app tests. Stop every process you start.
