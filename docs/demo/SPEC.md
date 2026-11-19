# Demo: manual level design vs GLDtk (side-by-side video)

**Goal:** a video that shows building **the same level** in LDtk in two ways: by hand, and
with the GLDtk sidebar. Both sides show elapsed time and a step counter, and the video ends
with a summary card.

The user chose the manual side to be **scripted at human pace** (2026-10-17). It is labelled
"simulated at human pace" in the video.

The user first capped the manual recording at **5 minutes of real time** (2026-10-17, an
**estimate** for the rest; still available as `--budget=300`), then asked for the **full build**
(2026-10-18): the whole level at human pace with simplified tiles, run offline, checked for
playability afterwards. The full build is the main video (`demo/out_full/comparison.mp4`).

## Level
- **Prompt:** "A crypt: a long corridor with three skeletons, then a small labyrinth of
  catacombs with a treasure chest in a dead end, and a boss arena where the lich waits by the
  exit."
- **Size:** about 120×30 tiles. Use the default Game Profile.
- **Source:** the AI side is generated first. Its saved `.ldtk` is the **target** that the
  manual side rebuilds: the same collision grid, tiles and entities.

## AI side (real)
- Start from an empty project (a new `.ldtk` with GLDtk's layer setup) in LDtk with the
  sidebar.
- Type the prompt and press Generate.
  - The model is `gemini-3.1-flash-lite`, falling back to local qwen if Gemini is busy.
  - Record which model answered and show it in a caption.
- Record the real wall time from the click to the level shown.
- Then show one follow-up edit, "Make this section a… arena" on one section, then undo it.
  This shows the iteration speed.
- Steps counted are user actions only: clicks plus typed words or keystrokes.

## Manual side (scripted, human pace)
Drive LDtk's own editor UI (no GLDtk sidebar) through CDP / DOM events, the way a human would.
1. Create the level with the target size, and select the Collisions IntGrid layer.
2. Paint the collision grid.
   - Use the **rectangle tool** for large solid masses and the single-cell brush for details.
   - Decompose the target grid into rectangles greedily, as a sensible human would.
   - Paint hazard cells with the hazard IntGrid value.
3. Paint the Tiles layer: tile-picker selection, then rectangle fills per tile role, at the
   same human pace. If the Tiles layer is too fiddly to drive, rebuild collisions and
   entities only, and caption honestly: "tiles not included — would add more time".
4. Place every entity.
   - For each one: select the entity type, click its position, then set its fields in the
     instance panel (for example the Enemy kind, or the Chest contents).
5. Save.

Order: the whole structure first (level setup → big rock rectangles → rock details → hazards →
tiles), and only then the entities (Start, enemies, boss, chests/doors/keys, exit).

**5-minute cap (optional).** The run stops at the first action after 5:00 of real time. It logs progress on
every action-log line: share of the target collision cells painted, tile cells painted,
entities placed (n/N), steps so far and the share of the modeled work done.

**Manual estimate.** Count every action of the whole plan per phase (the same order and kinds
as the script: clicks, choices, drags, keystrokes, look pauses), multiply by the timing
constants, then scale by the measured real/modeled ratio of the 5:00 that was recorded (mouse
travel, LDtk redraws). It is labelled as an estimate everywhere.

**Human timing model** (applied as real waits, so the recorded time is honest):
- a click is 0.6 s;
- choosing a tool or layer, or switching panels, is 1.2 s;
- typing a field value is 0.25 s per character plus 1.5 s to think;
- a rectangle drag is 1.0 s;
- "look at the reference" pauses take 2 s per section.

Put the constants in one config at the top of the script, and show them in the summary card.

**Count:** every click, drag and keystroke.

**Not done by hand:** checking that the level is playable. GLDtk's level is verified while it is
generated; the manual level is checked only after the build (`demo/verify.py --rules-from
target.ldtk`, outside the recorded time) and the end card says so.

## Recording
- **Capture:** use CDP `Page.startScreencast` (no macOS screen-recording permission needed)
  for each run.
  - Save frames with their timestamps.
  - Build each video with ffmpeg at the true pace (variable frame rate to a constant 30 fps).
- **Action log:** write each run as JSONL (`t`, `action`, `count`), and use it for the
  overlays.

## Composition (ffmpeg)
- Make two panels side by side at 1920×1080 overall: the left is labelled "Manual (LDtk)",
  the right "GLDtk AI".
- **Overlays per panel:**
  - elapsed time as `mm:ss`, mapped to *real* time even when sped up;
  - a step counter;
  - captions for the current phase ("Painting collisions…", "Placing 3 skeletons…",
    "Generating…").
  - Build them from the action logs with an ASS subtitle file or drawtext.
- **Speed:**
  - The manual side is 5:00, so play it at ×1 or a small speed-up (for example ×3) with a
    badge, keeping the timer, the step counter and a "% done" overlay.
  - The AI side plays at ×1. When it finishes, its panel holds the final level with a
    "Done in N s" banner while the manual side keeps going.
  - Choose the speed so the whole video is about 60–120 s.
- **End card:** for the manual side "5:00 recorded, X% done" and "estimated full build ≈ mm:ss,
  ~N steps" (labelled as an estimate), next to the AI's real numbers; a table of real time,
  steps (clicks, drags, keys), whether the level is
  verified, and edit turnaround.
- **Outputs:**
  - `comparison.mp4` (H.264);
  - `manual_full.mp4` and `ai_full.mp4` (each at ×1);
  - `stats.json`;
  - a `poster.png` frame.

## Where things go
- **Scripts** are committed under `demo/`, with a README on how to re-run them:
  - `demo/record_ai.js`
  - `demo/record_manual.js`
  - `demo/compose.py`
- **Tracked deliverables for demo/presentation** are committed under `docs/demo/`:
  - `docs/demo/comparison.mp4` (3.9 MB — side-by-side Manual 5:57 vs. GLDtk 1:13)
  - `docs/demo/ai_full.mp4` (1.6 MB — full 1× GLDtk AI run)
  - `docs/demo/manual_full.mp4` (8.3 MB — full 1× manual LDtk build)
  - `docs/demo/poster.png` (poster frame)
  - `docs/demo/stats.json` (measured timings, steps, and verification summary)
- **Raw build directories** `demo/out/` (AI run, 5-minute variant) and `demo/out_full/` (full build intermediates, frames, logs) remain git-ignored.
- **Projects:** never use `playground/` or `demo_*`. Work in fresh project copies under the
  scratchpad or in `demo/out/`.
- **Port 8765:** check it is free first, and stop only the processes you started.
