# Demo video: manual level design vs GLDtk

Builds the side-by-side video described in `docs/demo/SPEC.md`: the same level made by hand in
LDtk's own editor (left) and with the GLDtk sidebar (right).

| File | What it does |
|---|---|
| `record_ai.js` | Real run. Opens an empty project, types the prompt in the sidebar, presses Generate (real LLM), then "Make this section a arena" on the labyrinth section and undoes it. Saves the generated file as `out/ai/target.ldtk`. |
| `record_manual.js` | Scripted run at human pace, capped at 5:00 of real time (`--budget`). Rebuilds `target.ldtk` in plain LDtk (sidebar hidden): resizes the level, paints collisions (big rock rectangles with Shift-drag, then details, then hazards), paints tiles (tile picker, then rectangles per tile), then places the entities (start, enemies, boss, items, exit) and sets their fields in the instance panel, saves. When the budget runs out it stops and estimates the full build. The timing constants (`HUMAN`) are at the top. |
| `compose.py` | ffmpeg composition: `comparison.mp4`, `manual_full.mp4`, `ai_full.mp4`, `stats.json`, `poster.png`. |
| `make_projects.py` | Start projects (`empty`, `blank`) and the manual plan (`plan`: greedy rectangles per IntGrid value and per tile, entity list). |
| `compare.py` | How closely the manual file matches the target (collision cells, tile cells, entities with their hand-set fields). |
| `lib.js` | Launches LDtk with Playwright's Electron driver, reaches the Haxe classes, records with CDP `Page.startScreencast`, logs actions, and paces user actions (`Human`). |

## Re-running

Needs ffmpeg (with libass), Node 22+, Playwright (`npm i -g playwright`, or set `PLAYWRIGHT` to its
folder; the npx cache is searched too), the debug renderer and the Python venv.

```bash
lsof -iTCP:8765 -sTCP:LISTEN            # must be empty
cd ldtk-src && HAXE_STD_PATH="/opt/homebrew/lib/haxe/std" /opt/homebrew/bin/haxe renderer.debug.hxml && cd ..
GLDTK_MEMORY_DIR=$(mktemp -d) .venv/bin/python server.py &    # own design memory, so the demo does not learn into yours

node demo/record_ai.js                                   # ~90 s, one LLM generate (gemini-3.1-flash-lite; DEMO_MODEL overrides)
node demo/record_manual.js demo/out/ai/target.ldtk       # 5:00 at human pace, no LLM (--budget=0: build it all, ~20 min)
.venv/bin/python demo/compose.py                         # --speed N to force the manual speed-up
```

Full manual build overnight, offline (no LLM, server or internet; reuses the recorded AI run,
keeps the Mac awake, retries if LDtk dies, then checks playability with `demo/verify.py` and composes
the video into `demo/out_full/`):

```bash
nohup demo/run_full_offline.sh >/dev/null 2>&1 &
tail -f demo/out_full/run.log
```

Dry runs of the manual script, without recording, on a few columns:

```bash
node demo/record_manual.js demo/out/ai/target.ldtk /tmp/m --region=0,20 --no-record [--no-tiles]
```

Everything is written to `demo/out/` (git-ignored). Stop the server afterwards.

## How it works, and what is honest about it

- **Real input.** The manual side uses real mouse and keyboard events (CDP `Input.dispatch*`) on
  LDtk's canvas and panels. Cell positions come from LDtk's camera (`Editor.ME.camera`), tile
  positions from the tile picker's zoom and scroll. The level is fitted with the mouse wheel and a
  middle-drag pan, which count as steps (the pan as a drag).
- **Human timing.** Every action waits as long as `HUMAN` says before it happens: click 0.6 s;
  tool, layer or panel 1.2 s; typing 0.25 s per character plus 1.5 s to think; rectangle drag
  1.0 s; 2 s to look at the reference per section. The recorded time is the real wall time.
- **Steps** are every click, drag and keystroke on both sides; the AI prompt is counted per
  keystroke too, and it is typed at the same pace.
- **Entity fields.** The manual side sets the fields a designer would (enemy archetype, facing,
  health, chest contents, start weapon…) when they differ from LDtk's default. GLDtk's own
  bookkeeping fields (`spec_id`, `section`, `anchor`, `gldtk_meta`, `sprite`, `sprite_tile`,
  `description`) are not typed.
- **Not done by hand:** checking that the level is playable. The video says the manual level
  is not verified; GLDtk's is verified and soft-lock-free.
- **Starting points.** The AI side starts from an empty GLDtk project (`make_projects.py empty`).
  The manual side starts from a project with the same definitions as the target (layers,
  IntGrid values, tileset, entity types: a one-time setup) and an empty 16×16 level.
- **5-minute cap and estimate.** The manual run stops at the first action after 5:00. Every
  action-log line carries the progress (collision and tile cells painted, entities placed, share
  of the modeled work). The full build is estimated by `estimateFull()`: every planned action of
  the whole level, in the script's order, times the timing constants, scaled by the measured
  real/modeled ratio of the recorded 5:00. The video and `stats.json` label it as an estimate.
  After the stop the file is saved outside the recording (not counted) so `compare.py` can
  measure what was built.
- `compare.py` checks the result: the manual file should match the target cell for cell.

## Reaching LDtk's internals

The renderer is one Haxe closure with no exports. `lib.js` takes a jQuery handler that Haxe bound
on `document`, follows `[[TargetFunction]]` to the real function, walks its `[[Scopes]]` over CDP
and stores the closure's `$hxClasses` registry as `window.__hx` (`__hx['page.Editor'].ME`). Haxe
properties are read through their getters (`get_curLevel()`, `get_adjustedZoom()`).
