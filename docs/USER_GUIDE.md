# GLDtk user guide

This guide is for level designers who have never used GLDtk. It covers installing it, the first run,
every part of the GLDtk sidebar, how to write prompts that give good levels, where GLDtk keeps its
files, and what to do when something goes wrong. Developers will find a short repository map at the end.

## Contents

1. [What GLDtk is](#1-what-gldtk-is)
2. [Installing](#2-installing)
3. [First run: Settings, provider and API key](#3-first-run-settings-provider-and-api-key)
4. [The sidebar, section by section](#4-the-sidebar-section-by-section)
5. [Building levels](#5-building-levels)
6. [Where things are on disk](#6-where-things-are-on-disk)
7. [Troubleshooting](#7-troubleshooting)
8. [Repository map (for developers)](#8-repository-map-for-developers)

---

## 1. What GLDtk is

GLDtk (Generative Level Design Toolkit) turns a text description into a playable 2D platformer level
inside [LDtk](https://ldtk.io), the open-source level editor. You type something like *"a forest with
5 coins and a slime, then a dark cave with a spike pit"*, press **Generate**, and a few seconds to a few
minutes later the level opens in LDtk: tiles, collisions, Start, Exit, enemies, pickups and all.

Two things make it different from "ask a chatbot for a map":

- **Every level is proven beatable before it is written.** An LLM writes a plan of the level (sections,
  platforms, entities). Deterministic code then builds the tile grid and a physics checker proves that
  the player can get from Start to Exit with *your* game's jump height, run speed and abilities. It also
  proves the player can never get stuck (no pit without a way out, no door whose key is behind it). If
  that proof fails, the level is repaired; if it still fails, nothing is written.
- **It tells you how much of your prompt it achieved.** Each request in the prompt ("5 coins", "a boss
  guarding the exit") becomes a line in a fidelity report with a ✅ / ⚠ / ❌ status and a reason.

After generating you keep working in LDtk as usual. You can hand-edit anything, regenerate only one
rectangle, one section or only the entities, play the level, see the route a player would take, and
undo any GLDtk change.

GLDtk is two programs that run together:

| Program | What it is | Where |
|---|---|---|
| The **backend** | A Python server that talks to the LLM and builds levels | `http://127.0.0.1:8765` |
| **LDtk with the GLDtk sidebar** | A fork of LDtk 1.5.3 with a GLDtk panel on the right | the LDtk window |

The packaged apps start both for you. When you build from source, `./start.sh` starts both.

---

## 2. Installing

### 2.1 macOS (.dmg)

1. Download the `.dmg` from the [GitHub Releases page](https://github.com/bekiryan/GLDtk/releases)
   (Apple Silicon Macs only).
2. Open the `.dmg` and drag **GLDtk** into **Applications**.
3. **The first time only:** the app is not signed by Apple, so a double-click shows *"GLDtk can't be
   opened because Apple cannot check it for malicious software"*. Instead, **right-click (or
   Control-click) GLDtk in Applications and choose Open**, then click **Open** in the dialog. macOS
   remembers this, and later launches work with a normal double-click.
   - On recent macOS versions the dialog may only offer *Done*. Then open **System Settings → Privacy &
     Security**, scroll down to the message about GLDtk and click **Open Anyway**.

The app contains the backend, so no Python, Node or terminal is needed. It starts the backend on port
8765 (or a free port if 8765 is taken) and stops it when you quit.

### 2.2 Windows (installer)

The Windows installer is built by GitHub Actions.

1. Go to the [GitHub Releases page](https://github.com/bekiryan/GLDtk/releases) and download the
   Windows installer (`.exe`) from the newest release.
   - No release yet, or you want the latest build? Open the repository's **Actions** tab, select the
     newest successful Windows build run, and download the installer from the **Artifacts** list at the
     bottom of the run page (you need to be signed in to GitHub). Artifacts come as a `.zip`; unzip it
     to get the `.exe`.
2. Run the installer. Like the Mac app it is not code-signed, so Windows SmartScreen may show
   *"Windows protected your PC"*. Click **More info → Run anyway**.
3. Start **GLDtk** from the Start menu.

### 2.3 Building from source

You need two repositories: [bekiryan/GLDtk](https://github.com/bekiryan/GLDtk) (the backend, this
repository) and [bekiryan/ldtk](https://github.com/bekiryan/ldtk) (the LDtk fork with the sidebar),
cloned into the `ldtk-src/` folder of the first one.

Requirements:

| Tool | Version | Used for |
|---|---|---|
| Git | any | cloning |
| Python | 3.11 or newer | the backend |
| Node.js + npm | current LTS | running LDtk (Electron) |
| [Haxe](https://haxe.org/download/) | 4.x | compiling the LDtk fork |
| [Ollama](https://ollama.com) | optional | the local LLM fallback |

#### macOS

```bash
# 1. Clone both repositories
git clone https://github.com/bekiryan/GLDtk.git
cd GLDtk
git clone https://github.com/bekiryan/ldtk.git ldtk-src

# 2. Python environment for the backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[server,dev]"

# 3. Haxe libraries for LDtk (once)
cd ldtk-src
haxe setup.hxml
cd "$(haxelib config)deepnightLibs/git" && git checkout e1237f8 && cd -   # the required pin, see below

# 4. Electron and the other npm packages (once; must be run inside app/)
cd app && npm i && cd ..

# 5. Compile LDtk with the sidebar
HAXE_STD_PATH="/opt/homebrew/lib/haxe/std" /opt/homebrew/bin/haxe main.hxml
HAXE_STD_PATH="/opt/homebrew/lib/haxe/std" /opt/homebrew/bin/haxe renderer.debug.hxml
cd ..

# 6. Run both programs
./start.sh
```

The paths `/opt/homebrew/...` are where Homebrew installs Haxe on Apple Silicon. If `haxe` is on your
`PATH` and finds its standard library, `haxe main.hxml` and `haxe renderer.debug.hxml` are enough.

Warnings about `WDeprecatedEnumAbstract` and `WDeprecated` are expected and harmless. Any real error
stops the build.

> **deepnightLibs pin.** The `deepnightLibs` Haxe library must stay at commit **`e1237f8`** (on the
> author's machine it lives in `~/haxelib/deepnightLibs/git/`). **Do not run `haxelib update` or
> `haxelib install deepnightLibs`**: a newer version has API changes and breaks the build. If the build
> suddenly fails with errors in `dn.*` classes, go to that folder and run `git checkout e1237f8` again.

`start.sh` activates `.venv`, starts `server.py` in the background, starts LDtk from `ldtk-src/app`
with `npm start`, and stops the backend when you close LDtk. You can also run them in two terminals:

```bash
source .venv/bin/activate && python server.py   # terminal 1: backend
cd ldtk-src/app && npm start                    # terminal 2: LDtk
```

You only need to rebuild the Haxe parts after pulling changes in `ldtk-src/`: `renderer.debug.hxml`
whenever the sidebar changes, `main.hxml` only when the Electron main process changes.

#### Windows

Use PowerShell. `start.sh` is a bash script, so on Windows start the two programs yourself.

```powershell
# 1. Clone both repositories
git clone https://github.com/bekiryan/GLDtk.git
cd GLDtk
git clone https://github.com/bekiryan/ldtk.git ldtk-src

# 2. Python environment
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[server,dev]"

# 3. Haxe libraries (once), then pin deepnightLibs
cd ldtk-src
haxe setup.hxml
haxelib config                      # prints the haxelib folder, e.g. C:\HaxeToolkit\haxe\lib\
cd <haxelib folder>\deepnightLibs\git
git checkout e1237f8
cd <back to>\GLDtk\ldtk-src

# 4. npm packages (inside app\)
cd app; npm i; cd ..

# 5. Compile
haxe main.hxml
haxe renderer.debug.hxml
cd ..
```

Then run, in two PowerShell windows:

```powershell
.venv\Scripts\Activate.ps1; python server.py      # window 1: backend
cd ldtk-src\app; npm start                        # window 2: LDtk
```

The same `deepnightLibs` warning applies: never run `haxelib update`.

---

## 3. First run: Settings, provider and API key

GLDtk needs an LLM. You can use a cloud model with an API key, a local model with Ollama, or both
(cloud first, local as the fallback).

### 3.1 The Settings fold

The GLDtk sidebar is on the right side of the LDtk window. The **AI** button at the window edge shows
or hides it. **SETTINGS** is the last fold at the bottom of the sidebar (hint: *provider, model, API
key*). On the first launch, when the selected provider has no key yet, GLDtk opens Settings by itself
and puts the cursor in the key field.

| Control | What it does |
|---|---|
| **Provider** (drop-down) | `gemini` (default), `anthropic`, `ollama`, `openai`, `deepseek`. Remembered between sessions. |
| **Model** (text field) | Leave empty for the provider's default model (shown as the placeholder, e.g. *default: gemini-3.8-flash*), or type a model name. |
| **API key** | Shown for cloud providers. The label names the provider (*Gemini API key*, *Anthropic API key*, ...) and says **saved** or **not set**. Paste the key and press **Save key**. For `ollama` the field is replaced by *Local model — no key needed*. |

Each provider has its own key, so you can save a Gemini key and a Claude key and switch between them
with the Provider drop-down. Saving calls `POST /settings`; the sidebar reads which keys exist with
`GET /settings`, which only answers *yes/no* per provider and never sends a key back.

| Provider | Key variable | Default model | Notes |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` | `gemini-3.8-flash` | Free tier available. Falls back to `gemini-3.1-flash-lite`, then to the local model. |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` | Paid. Very reliable structured output. |
| `ollama` | none | `llama3.1` | Fully local. Use `qwen2.5:14b` or larger for good levels. |
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | Best effort. |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-chat` | Best effort. |

### 3.2 Where keys are stored

Keys are written to a `.env` file in your per-user GLDtk folder, with permissions set to owner-only:

| System | File |
|---|---|
| macOS | `~/Library/Application Support/GLDtk/.env` |
| Windows | `%APPDATA%\GLDtk\.env` (usually `C:\Users\<you>\AppData\Roaming\GLDtk\.env`) |

When you run from source, the backend also reads a `.env` in the repository root (it is git-ignored).
The per-user file wins when both set the same variable, and variables already set in your shell win
over both. `GLDTK_CONFIG_DIR` moves the per-user folder somewhere else.

Never commit a `.env` file or paste a key into a prompt or an issue.

### 3.3 Getting a free Gemini key

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and sign in with a Google
   account.
2. Click **Create API key** and copy it.
3. In GLDtk: Settings → Provider `gemini` → paste into **Gemini API key** → **Save key**.

The free tier allows roughly 20 requests a day per Flash model, and Google's servers are sometimes
overloaded. One generate is usually one request, sometimes two or three when GLDtk has to re-prompt.
That is why the local fallback below is worth setting up.

### 3.4 The local fallback (Ollama)

When Gemini cannot answer (quota used up, server overloaded, model retired, timeout, no internet),
GLDtk automatically asks a local model instead. This also happens when you typed a Gemini model
yourself. The first warning of the result then says that the fallback answered.

1. Install [Ollama](https://ollama.com) and start it.
2. Download the default fallback model (about 9 GB):

   ```bash
   ollama pull qwen2.5:14b
   ```

3. That is all. The fallback is `ollama:qwen2.5:14b` by default. To use another model, set
   `GLDTK_LOCAL_FALLBACK=ollama:<model>` in the `.env`; `GLDTK_LOCAL_FALLBACK=off` turns the fallback off.

A local 14B model takes about 1 to 5 minutes per level on an Apple M-series Mac. GLDtk waits up to
900 seconds for one Ollama call. You can also choose `ollama` as the main provider in Settings and work
fully offline.

---

## 4. The sidebar, section by section

Open or create a `.ldtk` project first: the sidebar always works on the project that is open in LDtk,
and writes the result back into that same file. The file name appears near the top of the sidebar
(*No project open — open a .ldtk file first* when there is none). Save a new project once before
generating, so that it has a path.

From top to bottom the sidebar has: the title bar, the progress strip, the message history, the
**PROMPT** box, and then the folds **LEVEL**, **EDIT**, **GENERATION OPTIONS**, **GAME PROFILE**,
**DESIGN MEMORY** and **SETTINGS**. Click a fold's header to open or close it.

### 4.1 Title bar and message history

- **GLDtk** and a status word: *ready*, *generating…*, *saving…*, *reloading…*, *error*, *offline*.
- **clear** empties the message history (it does not touch any file).
- The **message history** shows your prompts and GLDtk's answers like a chat. Each answer is a result
  card (see [4.3](#43-the-result-card)). The history is kept between sessions.

### 4.2 PROMPT and Generate

Type a description and press **▶ Generate** (or **Ctrl+Enter** / **Cmd+Enter** in the box).

- Calls `POST /generate` with your text, the open project's path as `output_path`, a random seed, the
  provider and model from Settings, and the values from GENERATION OPTIONS.
- If you have unsaved changes, GLDtk saves the project first.
- Before writing, GLDtk takes a snapshot of the file for **Undo** (see [4.5](#45-undo-and-history)).
- When the server answers, the project is reloaded in LDtk about 400 ms later.
- **Generate replaces the levels in the file.** To change only part of a level, use EDIT instead.

While a job runs, the Generate button becomes **■ Cancel** (see [4.10](#410-progress-and-cancel)).

### 4.3 The result card

Every generate and edit adds a card to the history with:

| Part | Meaning |
|---|---|
| Header line | theme, number of platforms and moves, entity counts (`5×Pickup`, `2×Enemy`), and **beatable** or **not verified** |
| World line | for a multi-level world: its title, number of levels and world score |
| **Examples used** / **built from** | which pattern-library pieces were shown to the model or used to carve a section, with their licence |
| **Fidelity report** | one line per requirement taken from your prompt. ✅ satisfied, ⚠ approximated, ❌ impossible or missing, · unchecked (taste words such as "spooky" that cannot be measured). Click a line for the reason. *auto* marks items GLDtk filled in deterministically. The score counts satisfied as 1 and approximated as ½. |
| **Mission** | for prompts with keys, locks, ability gates or loops: each goal with ✓ or ✗ ("Key k1 → lock door1", "Gate needs double_jump") |
| **warnings** | layout notes, repairs, the model fallback, and so on |
| **Preview** | an ASCII picture of the level |
| **✓ Saved** | the file that was written |

A requirement marked ❌ *impossible* usually means the Game Profile does not allow it, for example a
prompt asks for a wall jump but wall jump is off in the profile.

### 4.4 LEVEL fold: levels, Play, route, difficulty

Hint: *play, route, difficulty*.

**Level list (multi-level worlds).** Shown when the file has two or more levels. Each row has the
LDtk identifier, the level's title and its fidelity score. Click a row to open that level; *Editing:*
shows which level the edits, route and chart act on. **↻ Regenerate this level** runs a new generate
for only the open level with its own stored prompt (`POST /generate` with `level`), keeping the other
levels and the Exit→Start links between them.

**▶ Play.** LDtk has no play mode, so GLDtk adds one. It opens the open level in a game window using
the Game Profile's physics and abilities (the same numbers the verifier uses). Nothing is written to
the file. Controls:

| Key | Action |
|---|---|
| ← / → or A / D | run |
| Space, W or ↑ | jump (hold for a higher jump) |
| Shift | dash (when the profile has dash) |
| R | restart |
| G | show or hide the **route ghost** (the verified route as a faint line) |
| Esc | close |

You have 3 lives. Coins and gems are counted, a health pickup adds a life, hazards, enemies and falling
out of the level cost a life and respawn you at the last checkpoint (or the Start). An Exit that needs a
key stays locked until you pick the key up. Reaching the Exit shows your time and pickups. Unsaved
changes are saved first.

**Show route.** A check box that draws the cheapest verified Start→Exit route on the canvas: walks,
jumps, falls and which ability each move uses. Places where the player could get stuck are drawn in red.
The line next to it summarises the route (*Route: 23 moves (6 jumps, 1 ability move)*) and warns about
unreachable required entities or stuck spots. It uses `GET /route` for the saved file; after you
hand-edit tiles it refreshes with `POST /route`, sending the project as it is in LDtk, so you see
straight away whether your edit broke the path.

**Difficulty chart.** One bar per section, in level order, scored 0–10, with the overall score in the
title. A ⚠ under a bar means the measured difficulty does not fit what the section was asked to be;
level-wide warnings (a sudden spike, a long flat stretch) appear under the chart. Hover a bar for the
details and to flash that section on the canvas. Data from `GET /difficulty`; no LLM call.

### 4.5 EDIT fold: change part of a level

Edits regenerate only part of the level. Everything outside the edited part, including your hand
edits, stays exactly as it was. The places where the player's path enters and leaves the area are kept
as fixed seams, and the whole level is verified again before it is written. If the edit cannot be made
beatable, the file is not changed and the card says *The level was not changed*.

Pick a mode with the four buttons at the top of the fold, then describe the change in the text box
(*What should be there…*) and press the action button (**Ctrl+Enter** works too).

| Mode | How to choose the target | Button | Endpoint |
|---|---|---|---|
| **Region** | **Select area**, then drag a rectangle on the level (Esc cancels, × clears it). Shown in yellow. | Regenerate area | `POST /edit/region` |
| **Section** | Pick a section from the drop-down (it shows each section's difficulty and structure). Shown in blue. | Regenerate section | `POST /edit/section` |
| **Entities** | Select entities in LDtk, or drag an area, or nothing for the whole level. Only entities change; tiles stay. | Edit entities | `POST /edit/entities` |
| **Describe** | Type where, in words: *"the gap before the boss"*. | Find location | `POST /edit/locate`, then `POST /edit/region` |

In **Describe** mode GLDtk first finds the area and draws it in orange with an explanation. Check it,
then press **Apply here** (or **Cancel**). Apply runs a normal region edit on that rectangle.

**Section-only controls** (Section mode):

- **Make this section a…**: choose `corridor`, `parkour`, `labyrinth`, `arena`, `shaft`, `hub` or
  `open` to carve the section again as that structure. This needs **no LLM call** and no prompt, so it
  is instant and uses no quota.
- **Difficulty** slider (1–10) with **Auto**: the difficulty target for this section only. Auto leaves
  it as it is.

**↶ Undo last GLDtk edit** (at the bottom of the fold) restores the file as it was before the last
GLDtk generate, edit, chunk append or undo-able change. The small number on the button is how many
snapshots are left; the tooltip says what the next undo restores. Uses `GET /history` to list
snapshots and `POST /undo` to restore. If you have unsaved changes in LDtk, GLDtk asks first, because
they will be lost. GLDtk keeps the last 30 snapshots per file.

Undo only covers GLDtk's own changes. Use LDtk's normal undo (Ctrl+Z) for your hand edits.

### 4.6 GENERATION OPTIONS fold

These settings are remembered and sent with every generate (and, where it makes sense, with edits).

| Option | Values | What it does | Request field |
|---|---|---|---|
| **Levels** | Auto, 1–8 | Number of levels. Auto: one level, unless the prompt describes several. 2–8 builds a world of exactly that many levels. | `levels` |
| **Difficulty** | 1–10, or Auto | Target complexity for the whole level: enemy and hazard density, jump tightness, branching, mission loops. After building, GLDtk measures the result and adjusts it until it is close to the target. ↺ resets to 5. | `complexity` (sent as 0–1) |
| **Rock fill** | 5–100 %, or Off | Fills unused space with natural cave-shaped rock. The route, its jump arcs and headroom are never touched. | `fill` |
| **Examples** | Off, 1–5 | Shows the model that many similar pieces from your pattern library (needs a built library, see [6](#6-where-things-are-on-disk)). | `patterns` |
| **Use real level pieces** | on (default) / off | Lets GLDtk carve a section from a matching real level piece in the pattern library instead of its own generator. No LLM call. | `stamp_patterns` |
| **Background decor** | off (default) / on | Adds a decorative Background layer (caves, strata, stalactites). No effect on gameplay. | `background` |
| **Chunks** + **Generate next chunks** | 1–10, optional theme | Appends endless chunks to the open file (see below). | `POST /chunks` |

**Endless chunks.** **Generate next chunks** adds N new levels named `Chunk_0`, `Chunk_1`, … after
the last chunk in the file. They are made from seeded noise (no LLM call), joined to their neighbours
through fixed openings, and each one is verified on its own and across its seams. The optional theme
is one of `cave` (default), `dungeon`, `forest`, `sky`, `ice`, `desert`, `lava`, `castle`. The card
lists the chunks added and whether each is beatable. Use it for endless-runner style content or to
extend a world quickly.

### 4.7 GAME PROFILE fold

The Game Profile describes *your* game's player, and every level is verified against it. It is saved
as `gldtk.game.json` next to the `.ldtk` file, so each project has its own. The fold loads it with
`GET /profile` and **Save profile** writes it with `PUT /profile`. Invalid values are marked next to the
field.

| Group | Fields (default) |
|---|---|
| Physics | Gravity 1800 px/s², Jump speed 500 px/s, Run speed 220 px/s, Walk step 8 px |
| Double jump (toggle, off) | Jump speed 340 px/s |
| Dash (toggle, off) | Distance 64 px, Usable mid-air (on) |
| Wall jump (toggle, off) | Kick speed 460 px/s, Max climb 64 px, Push 32 px |

With the defaults and 16 px tiles, a jump is about 4.3 tiles high and 7.6 tiles across. See
[5.5](#55-the-game-profile-gldtkgamejson) for what the numbers mean and for the fields that are only
in the file.

### 4.8 DESIGN MEMORY fold

GLDtk learns your taste from what you do: hand edits to its output, undos, accepted edits and Game
Profile changes. These are turned into short rules (*"prefers 2–3 enemies per room"*) that are added to
later prompts. Memory is local and shared across all your projects.

- **Learn from my edits**: turns memory on or off.
- Each **rule** has a check box (use it or not), editable text, its source and a confidence, and **×**
  to delete it.
- A few statistics (typical gap, enemy spacing, kinds you remove, undo rate).
- **Scan now** compares the open file with what GLDtk last wrote and records your hand edits
  (`POST /memory/scan`).
- **Reset** deletes all rules and statistics after a confirmation (`DELETE /memory`).

Reading uses `GET /memory`, changes use `PUT /memory`. Only the short rule text ever leaves your
computer, inside an LLM prompt.

### 4.9 SETTINGS fold

See [section 3](#3-first-run-settings-provider-and-api-key). The provider list comes from
`GET /providers`.

### 4.10 Progress and Cancel

While a generate, edit or locate runs, the progress strip under the title bar shows the current step
(LLM attempt, retry, model fallback, verification, repair) and the elapsed time. **Log** opens the full
list of steps. The sidebar polls `GET /progress` every second with the job's id.

To stop, press **■ Cancel** (the Generate or edit button while a job runs) or **Esc** in the prompt
box. This calls `POST /cancel`. The server stops at its next checkpoint, which may mean waiting for the
current LLM call to return, and **writes nothing**. If the job was already finished, the card says the
cancel came too late and the result is kept (use Undo to remove it).

### 4.11 Bundled LDtk samples: Save as first

LDtk comes with sample projects. They live inside the LDtk app folder (inside `GLDtk.app` on a Mac),
where any change would be lost at the next update. GLDtk never writes there. If you press Generate,
an edit button, Apply here, a structure change, Save profile, Generate next chunks or Regenerate this
level while a sample is open, GLDtk says *"This is a bundled LDtk sample, which GLDtk does not change.
Choose where to save your copy."* and opens a **Save as** dialog (suggested name
`<sample>-gldtk.ldtk` in your Documents folder). After you pick a place outside the app folder, the
copy is saved, the open project switches to it, and the action you asked for continues on the copy.
Play does not write anything, so it works on a sample directly. The server refuses such writes too (HTTP 400), as a
safety net.

### 4.12 Endpoint summary

| Sidebar action | Endpoint |
|---|---|
| Generate, Regenerate this level | `POST /generate` |
| Region / Section / Entities edit | `POST /edit/region`, `/edit/section`, `/edit/entities` |
| Describe → Find location | `POST /edit/locate` |
| Undo, snapshot list | `POST /undo`, `GET /history` |
| Show route | `GET /route`, `POST /route` |
| Difficulty chart | `GET /difficulty` |
| Generate next chunks | `POST /chunks` |
| Progress, Cancel | `GET /progress`, `POST /cancel` |
| Game Profile | `GET /profile`, `PUT /profile` |
| Design memory | `GET /memory`, `PUT /memory`, `DELETE /memory`, `POST /memory/scan` |
| Settings | `GET /providers`, `GET /settings`, `POST /settings` |

---

## 5. Building levels

### 5.1 Step by step

1. **Start GLDtk** (the app, or `./start.sh`) and check that the status says *ready*.
2. **Create a project:** in LDtk, *New project*, and save it in a folder of its own (for example
   `~/Levels/forest/forest.ldtk`). GLDtk will put `gldtk_assets/`, `gldtk.game.json` and
   `.gldtk_history/` next to it.
3. **Set the Game Profile** (optional but recommended): open GAME PROFILE, enter your game's gravity,
   jump speed and run speed, switch on the abilities your player has, and press **Save profile**.
   Do this before generating, because levels are built for these numbers.
4. **Write a prompt** in PROMPT. Start simple: one or two sections, a few entities.
5. **Press Generate.** Watch the progress strip. A cloud model takes seconds, a local one minutes.
6. **Read the result card:** is it *beatable*? Which fidelity lines are ⚠ or ❌, and why?
7. **Look at it:** open LEVEL, tick **Show route**, glance at the difficulty chart, press **▶ Play**.
8. **Iterate** with EDIT (see [5.4](#54-iterating-with-edits)) rather than generating everything again.
   If an edit is worse, **↶ Undo**.
9. **Finish by hand** in LDtk: tweak tiles, move entities. Tick Show route after hand edits to make
   sure the path still works.

### 5.2 Example prompts

Copy these into PROMPT to see what GLDtk can do. Each one shows a different feature.

1. **Mixed structures, three themes**

   > A sunny forest with 5 coins and a slime, then a dark cave with 2 bats, a spike pit and a treasure
   > chest, and finally a castle with a skeleton boss guarding the exit. The player starts with a sword.

2. **Labyrinth and arena**

   > An underground dungeon: first a twisting labyrinth with dead ends and 3 gems hidden in side
   > passages, then a big arena room where the player fights 4 goblins, then a short corridor to the exit.

3. **Vertical climb**

   > A tall frozen tower. The player climbs a shaft with narrow ice ledges, 2 checkpoints on the way up,
   > a bat every few floors, and the exit at the very top.

4. **Key and lock**

   > A castle level where the exit door is locked. The key is at the end of a side branch in the
   > dungeon section; the locked door is in the throne room at the end. Add 2 skeletons and a lever.

5. **Ability gate** (switch on Double jump in the Game Profile first)

   > A sky level. The first part is easy walking over clouds. Halfway there is a double jump pickup, and
   > after it a wide gap that can only be crossed with the double jump. 6 coins, 1 harpy.

6. **Hazards, hard parkour**

   > A lava cave for experienced players: a parkour section with long jumps over lava pools and spikes,
   > a turret shooting across the gaps, a health potion before the hardest jump, then a calm corridor to
   > the exit.

7. **Hub with loops**

   > A desert temple with a central hub room and three exits. Each branch has a gem at its end; one
   > branch loops back to the hub through a shortcut door. The exit opens from the hub.

8. **A world of several levels** (or set Levels to 3)

   > A 3-level world: level 1 is an easy forest tutorial with coins, level 2 a cave with bats and a
   > spike pit, level 3 a castle with a boss before the exit.

### 5.3 Tips for writing prompts

- **Describe the level in order**, left to right (or bottom to top): "first…, then…, finally…". Each
  part becomes a section.
- **Name the structure** when you care about the shape: *corridor*, *parkour*, *labyrinth* / *maze*,
  *arena* / *big room*, *shaft* / *climb*, *hub*, *open ground*.
- **Give numbers**: "5 coins", "2 bats", "3 checkpoints". Numbers are checked exactly in the fidelity
  report; "some coins" is not.
- **Use plain entity words.** GLDtk knows: coin / gold, gem / crystal, heart / potion / health, ammo,
  powerup / mushroom / star, key, sword / bow / gun / staff and other weapons, checkpoint / save /
  bonfire, chest, door / gate, lever, switch / button, sign, NPC / villager / merchant / wizard, enemies
  (slime, goblin, skeleton …), flyers (bat, bird, harpy, ghost), turret / cannon, boss.
- **Say where things go**: "a boss guarding the exit", "the key in the cave, the door in the castle".
  Placement between sections is checked structurally.
- **Ask for long jumps explicitly.** By default GLDtk prefers walking and short, comfortable hops. Say
  *parkour*, *long jumps* or *for experienced players* for a section where jumps should go up to the
  physics limit. *Easy*, *tutorial* or *walking* gives the gentlest style.
- **Abilities must match the Game Profile.** A prompt that needs a double jump while double jump is off
  is reported as ❌. Turn the ability on first.
- **Mood words** ("spooky", "cosy") pick the theme and are shown as · unchecked: fine to use, but they
  cannot be verified.
- **Keep the first prompt short**, then add detail with edits. A huge prompt is more likely to be only
  partly satisfied.
- **Use Difficulty** in GENERATION OPTIONS for overall challenge instead of writing "harder" everywhere.

### 5.4 Iterating with edits

| You want to… | Do this |
|---|---|
| Change the shape of one section without spending quota | Section mode → pick it → **Make this section a…** |
| Make one section harder or easier | Section mode → Difficulty slider → describe the change → Regenerate section |
| Redo one area | Region mode → Select area → drag → *"a pit with spikes and two floating platforms"* |
| Change what is in the level, not its tiles | Entities mode → *"make the bats stronger"*, *"add 3 coins here"*, *"remove the turret"* |
| You don't know the coordinates | Describe mode → *"the gap before the boss"* → check the orange box → Apply here |
| Regenerate one level of a world | LEVEL → click the level → ↻ Regenerate this level |
| Try a different random version | Generate again; each generate uses a new random seed |
| Go back | ↶ Undo last GLDtk edit (repeatable, up to 30 steps) |

Edits respect your hand edits outside the edited area, so it is safe to mix hand work and edits.

### 5.5 The Game Profile (`gldtk.game.json`)

The profile file sits next to your `.ldtk` file. It is created the first time you save the profile;
without it the defaults are used. The sidebar edits the physics and abilities. The `player` and
`design` groups are only in the file; edit them in a text editor while LDtk is closed or before the next
generate.

```json
{
  "tile_size": 16,
  "physics":   { "gravity": 1800, "jump_v": 500, "run_speed": 220, "walk_step_px": 8 },
  "abilities": {
    "double_jump": { "enabled": false, "jump_v": 340 },
    "dash":        { "enabled": false, "distance_px": 64, "air": true },
    "wall_jump":   { "enabled": false, "jump_v": 460, "max_climb_px": 64, "push_px": 32 }
  },
  "player": { "width_px": 12, "height_px": 24 },
  "design": { "comfort_hop_tiles": 4, "comfort_rise_tiles": 3, "comfort_drop_tiles": 6 }
}
```

| Field | Meaning |
|---|---|
| `physics.gravity` | downward acceleration, px/s². Higher = shorter, faster jumps. |
| `physics.jump_v` | jump launch speed, px/s. Jump height = `jump_v² / (2·gravity)`. |
| `physics.run_speed` | top horizontal speed, px/s (also in the air). Jump reach ≈ `run_speed · 2·jump_v / gravity`. |
| `physics.walk_step_px` | the biggest step up or down that counts as walking, not jumping. |
| `abilities.*.enabled` | which abilities the player has. Only enabled abilities are used by the verifier and the route. |
| `double_jump.jump_v` | speed of the second, mid-air jump. |
| `dash.distance_px`, `dash.air` | dash length, and whether it works in the air. |
| `wall_jump.*` | kick speed, extra height a wall jump can gain, extra horizontal push. |
| `player.width_px`, `height_px` | the player's hitbox, used to check headroom and arcs. |
| `design.comfort_hop_tiles` | the gap a *normal* jump should cross (empty columns). |
| `design.comfort_rise_tiles` | how far up a normal jump should go. |
| `design.comfort_drop_tiles` | how far a normal fall should go. |

The physics numbers say what is *possible*; the `design` comfort limits say what a *normal* move
should be. The route prefers walking, and a comfort pass adds platforms so most moves stay within the
comfort limits. Only sections in the `parkour` style use jumps up to the physics limit.

When you change the profile, existing levels are not rebuilt. Generate or edit again to get levels
for the new numbers; Show route will tell you if an old level no longer works.

---

## 6. Where things are on disk

### Next to your project

| Path | What it is | Keep it? |
|---|---|---|
| `my_level.ldtk` | The LDtk project. GLDtk's state (prompt, sections, requirements, spec, profile, version) is stored in the level's custom fields `gldtk_*`, so this file is the only source of truth. | yes |
| `gldtk_assets/themes/*.png` | The tile sheets of the themes the level uses (copied from GLDtk's CC0 asset library) | yes: without it the tiles are blank |
| `gldtk_assets/sprites.png` | Entity icons | yes |
| `gldtk.game.json` | The Game Profile | yes |
| `.gldtk_history/<project name>/` | Undo snapshots, the last 30 | optional; delete to free space (you lose GLDtk undo) |

Move or share a project by copying the whole folder.

### Per-user folder

| Contents | macOS | Windows |
|---|---|---|
| Base folder | `~/Library/Application Support/GLDtk/` | `%APPDATA%\GLDtk\` |
| API keys, fallback setting | `.env` | `.env` |
| Design memory | `memory/` (`events.jsonl`, `preferences.json`) | `memory\` |
| Pattern library index | `patterns/` | `patterns\` |
| Downloaded pattern sources | `sources/` | `sources\` |

On Linux the base folder is `~/.local/share/GLDtk/`. Overrides: `GLDTK_CONFIG_DIR` (settings and
`.env`), `GLDTK_MEMORY_DIR`, `GLDTK_PATTERNS_DIR`.

The pattern library is empty until someone builds it (developers: `python -m patterns.build`, see the
[README](../README.md#pattern-library)). With an empty library, **Examples** and **Use real level
pieces** simply have no effect.

### Environment variables

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` | provider keys (set from Settings) |
| `GLDTK_LOCAL_FALLBACK` | local fallback model, e.g. `ollama:qwen2.5:14b`; `off` disables |
| `GLDTK_CONFIG_DIR`, `GLDTK_MEMORY_DIR`, `GLDTK_PATTERNS_DIR` | move the per-user folders |
| `GLDTK_HOST`, `GLDTK_PORT` | backend address (default `127.0.0.1:8765`) |

---

## 7. Troubleshooting

| Problem | Cause and fix |
|---|---|
| **"Cannot reach GLDtk server — is python server.py running?"**, status *offline* | The backend is not running. From source: start it with `./start.sh` or `python server.py` and look at its terminal for errors. App: quit and reopen GLDtk. |
| **HTTP 429** (quota) from Gemini | The free tier's daily requests are used up. GLDtk already tried `gemini-3.1-flash-lite` and the local fallback. Pull the fallback model (`ollama pull qwen2.5:14b`) and make sure Ollama is running, switch provider, or wait until the quota resets (daily). |
| **"Gemini is busy (HTTP 503)"** | Google's servers are overloaded. Not a GLDtk bug. Try again in a minute, or let the local fallback answer. |
| **HTTP 404 model not found** (Gemini) | The model you typed was retired or misspelled. Clear the Model field to use the default. |
| **Ollama: "model not found"** | Run `ollama pull <model>` and check with `ollama list`. |
| **Ollama times out or is very slow** | A 14B model needs 1–5 minutes per level and GLDtk waits up to 900 s per call. Close other heavy apps, keep the prompt short, or use a smaller model (`ollama pull qwen2.5:7b` and set `GLDTK_LOCAL_FALLBACK=ollama:qwen2.5:7b`), knowing smaller models produce worse plans. Check that `ollama serve` is running. |
| **Port 8765 already in use** (`address already in use` when starting `server.py`) | Another backend is still running, often from an earlier `start.sh` or the app. macOS: `lsof -i :8765` then `kill <PID>`. Windows: `netstat -ano \| findstr 8765` then `taskkill /PID <PID> /F`. Or run on another port: start the backend with `GLDTK_PORT=8766 python server.py` and LDtk with the same `GLDTK_PORT=8766` in its environment. (The packaged app picks a free port by itself.) |
| **The level is blank in LDtk** (no tiles, or a grey canvas) | The tile sheets were not found. Keep `gldtk_assets/` next to the `.ldtk` (copy the whole folder when moving a project). Then reload the project. If it persists, generate again; the backend copies the sheets each time. |
| **"not verified"**, a soft-lock error, or *The level was not changed* | GLDtk could not prove the level or edit beatable with the current Game Profile, so nothing was written. Try again (new seed), simplify the request, or check that the profile allows what the prompt asks (abilities, jump height). |
| **A requirement shows ❌ impossible** | Usually the Game Profile forbids it (ability off, gap wider than a jump). Change the profile or the prompt. |
| **"Open a .ldtk project in LDtk first"** | No project is open, or a new project was never saved. Save it once. |
| **"Save your changes in LDtk first"** | GLDtk tried to save before an edit and could not. Save manually (Ctrl+S) and retry. |
| **macOS: "GLDtk can't be opened because Apple cannot check it"** | The app is unsigned. Right-click → Open, or System Settings → Privacy & Security → Open Anyway. See [2.1](#21-macos-dmg). |
| **Windows: "Windows protected your PC"** | The installer is unsigned. More info → Run anyway. |
| **Haxe build fails with errors in `dn.*`** | `deepnightLibs` moved off the pinned commit. `git checkout e1237f8` in its haxelib folder. Never run `haxelib update`. |
| **The sidebar is missing after building from source** | The renderer was not rebuilt, or the release build overwrote it. Run `haxe renderer.debug.hxml` in `ldtk-src/` again. |

---

## 8. Repository map (for developers)

| Package / file | Purpose |
|---|---|
| `server.py` | FastAPI backend: all HTTP endpoints, settings, `.env` loading |
| `api/` | request and response models (`models.py`), job progress, the bundled-samples guard, art routes |
| `spec/` | `LevelSpec` schema the LLM fills in, validation, entity catalog and synonyms |
| `llm/` | prompts, `LLMController` (generate → validate → patch → re-prompt), symbolic patcher, `providers/` (gemini, anthropic, ollama, openai, deepseek, fallback) |
| `pipeline/` | `build_level` (spec → verified level), completeness, complexity loop, worlds, endless chunks |
| `layout/` | spec layout, comfort pass, densify, rock fill and noise, route, soft-lock detection and repair, grid verification and repair, LDtk export and reader |
| `layout/terrain/` | structure generators: corridor, parkour, labyrinth, arena, shaft, hub, open |
| `mission/` | mission graph grammar (keys, locks, gates, loops), space embedding, item-state verification |
| `symbolic/` | level graph schema, physics oracle, grid → graph extraction |
| `game/` | Game Profile (`gldtk.game.json`) |
| `fidelity/` | requirement checker, fidelity report, deterministic fill, difficulty measurement |
| `edit/` | region / section / entity editing, seams, stitching, undo history |
| `aesthetic/` | themes, autotiling, sprites, asset library loader |
| `assets/` | the CC0 tile themes and sprites (`manifest.json`, `LICENSES.md`) |
| `patterns/` | pattern library: ingest, classify, label, embed, retrieve, stamp |
| `memory/` | design memory: event log, hand-edit diffs, analysis, preference rules |
| `art/` | optional AI theme and sprite generation |
| `eval/` | evaluation harness and prompt suite |
| `packaging/`, `scripts/` | PyInstaller spec, Mac app build, asset sheet build |
| `tests/` | offline test suite (`pytest`, fake LLM with recorded replies) |
| `ldtk-src/` | the LDtk fork (separate repository); the sidebar is `src/electron.renderer/ui/GldtkSidebar.hx`, play mode is `ui/gldtk/` |
| `example.py` | generate a level from the command line |

More detail: [README.md](../README.md) (architecture, endpoints, evaluation), [CLAUDE.md](../CLAUDE.md)
(build notes), [docs/v2/DESIGN.md](v2/DESIGN.md) (design).
