# GLDtk — Generative Level Design Toolkit

GLDtk turns a text prompt into a **playable, statically verified 2D platformer level** directly inside [LDtk](https://ldtk.io) via a native Haxe/Electron sidebar. An LLM handles creative structure and semantics, while deterministic terrain carving, a closed-form kinematic physics oracle, and Tarjan SCC soft-lock analysis guarantee every written level is beatable.

---

## How to Run on Desktop

GLDtk has two parts — the Python neuro-symbolic backend (`GLDtk`) and the forked LDtk desktop editor with the AI sidebar (`ldtk`). You can either run the **all-in-one desktop installer** or **run both repos from source**:

### Option A — Prebuilt Desktop App (Recommended, 1-click)

No repository cloning, Python, Node.js, or Haxe setup required — the `.app` / `.exe` bundles both the LDtk editor and the Python `gldtk-server` backend:

- **macOS (Apple Silicon):** Open [`ldtk-src/app/redist/GLDtk-1.5.3-arm64.dmg`](ldtk-src/app/redist/GLDtk-1.5.3-arm64.dmg) (or download from [GitHub Releases](https://github.com/bekiryan/GLDtk/releases)), drag **GLDtk** to **Applications**, right-click → **Open** on first launch, and set your free [Gemini API key](https://aistudio.google.com/apikey) (or local [Ollama](https://ollama.com)) in **Settings**.
- **Windows:** Download the installer `.exe` from [GitHub Releases](https://github.com/bekiryan/GLDtk/releases) (or GitHub Actions artifacts). See [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md#2-installing) for details.

### Option B — Run from Source (2 Repositories: `GLDtk` + `ldtk`)

Yes — running from source uses **2 repositories**:
1. [`bekiryan/GLDtk`](https://github.com/bekiryan/GLDtk) — the Python backend (`server.py`, pipeline, A* verifier, RAG)
2. [`bekiryan/ldtk`](https://github.com/bekiryan/ldtk) — the LDtk 1.5.3 Haxe/Electron fork with the GLDtk sidebar, cloned inside `GLDtk/` as `ldtk-src/`

```bash
# 1. Clone both repos (ldtk goes inside GLDtk as ldtk-src/)
git clone https://github.com/bekiryan/GLDtk.git
cd GLDtk
git clone https://github.com/bekiryan/ldtk.git ldtk-src

# 2. One-time Python & Electron setup (requires Python 3.11+ & Node.js)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[server,dev]"
(cd ldtk-src/app && npm install)

# 3. Configure an LLM provider (cloud key or local Ollama fallback)
echo 'GEMINI_API_KEY=your-key' > .env   # or: ollama pull qwen2.5:14b

# 4. Launch both the Python backend (:8765) and the LDtk desktop app
./start.sh
```

*(On a fresh `ldtk-src/` clone where `renderer.debug.js` / `client.js` are not yet compiled, also run Haxe setup & compilation once per [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md#23-building-from-source). To start the two processes manually instead of `./start.sh`, run `source .venv/bin/activate && python server.py` and `cd ldtk-src/app && npm start` in separate terminals.)*

### Run Tests & Offline Ablations

```bash
source .venv/bin/activate
pytest -q                               # full offline test suite (750+ tests)
python -m eval.ablation                 # offline replay ablation suite (A1–A5)
```

---

## Main Components

| Component | Path | Purpose |
|---|---|---|
| **LDtk Native Sidebar & Play Mode** | [`ldtk-src/`](ldtk-src/) | Haxe/Electron fork of LDtk 1.5.3 with prompt UI, area lasso, Route Overlay, Difficulty Curve, and 60 Hz Play Mode |
| **FastAPI Backend & Contracts** | [`server.py`](server.py), [`api/`](api/) | Local HTTP API (`:8765`) for generation, regional edits, route/difficulty inspection, chunks, and memory |
| **LLM Controller & Patcher** | [`llm/`](llm/), [`spec/`](spec/) | Schema-enforced `LevelSpec` generation (Gemini/Claude/Ollama fallback) + deterministic `SymbolicPatcher` |
| **Completeness & Fidelity** | [`fidelity/`](fidelity/), [`pipeline/completeness.py`](pipeline/completeness.py) | Prompt requirement extraction, single-tower collapse, deterministic fill, and 5-factor difficulty scoring |
| **Pipeline, Worlds & Chunks** | [`pipeline/`](pipeline/) | 11-stage orchestrator (`build_level`), closed-loop complexity tuning, multi-level worlds, endless chunks |
| **Terrain Carvers & Layout** | [`layout/`](layout/), [`layout/terrain/`](layout/terrain/) | 7 rock carvers (`corridor`, `parkour`, `labyrinth`, `arena`, `shaft`, `hub`, `open`), comfort pass, noise rock fill, LDtk 1.5.3 export |
| **Physics Oracle & Soft-Lock Solver** | [`symbolic/`](symbolic/), [`game/`](game/), [`layout/softlock.py`](layout/softlock.py) | Swept `12×24 px` hitbox kinematics (double jump, dash, wall kick, gravity) + Tarjan SCC & Item-State deadlock proof |
| **Mission Grammar & Regional Edit** | [`mission/`](mission/), [`edit/`](edit/) | Dormans lock-key/ability mission rewriting + seam-locked regional regeneration with 30-step undo history |
| **RAG Patterns & Design Memory** | [`patterns/`](patterns/), [`memory/`](memory/), [`aesthetic/`](aesthetic/) | 3,336 open-licensed pattern windows + cross-project edit preference learning + CC0 Kenney autotiles & sprites |
| **Evaluation & Demo Harness** | [`eval/`](eval/), [`demo/`](demo/) | 35-prompt benchmark suite, A1–A5 offline replay ablations, and side-by-side Playwright demo recorder |

---

## Architecture Diagrams (Draw.io)

![GLDtk High-Level Neuro-Symbolic Pipeline DAG](docs/architecture/01_pipeline_dag.png)

- **All-in-one 3-page Draw.io workbook:** [`docs/architecture/gldtk_architecture_master.drawio`](docs/architecture/gldtk_architecture_master.drawio) · [Diagram gallery & SVG/PNG links](docs/architecture/README.md)
- **Pipeline DAG:** [`01_pipeline_dag.drawio`](docs/architecture/01_pipeline_dag.drawio) ([SVG](docs/architecture/01_pipeline_dag.svg) · [PNG](docs/architecture/01_pipeline_dag.png))
- **System Architecture:** [`02_system_architecture.drawio`](docs/architecture/02_system_architecture.drawio) ([SVG](docs/architecture/02_system_architecture.svg) · [PNG](docs/architecture/02_system_architecture.png))
- **Verification & Regional Editing Flows:** [`03_verification_and_mission_dags.drawio`](docs/architecture/03_verification_and_mission_dags.drawio) ([SVG](docs/architecture/03_verification_and_mission_dags.svg) · [PNG](docs/architecture/03_verification_and_mission_dags.png))

---

## Demo Video

[![Manual vs GLDtk Side-by-Side Demo](docs/demo/poster.png)](docs/demo/comparison.mp4)

- **Side-by-side comparison (Manual 5m 57s / 311 steps vs. GLDtk AI 1m 13s / 172 steps — 4.89× speedup):** [`docs/demo/comparison.mp4`](docs/demo/comparison.mp4)
- **Full 1× recordings & stats:** [`docs/demo/ai_full.mp4`](docs/demo/ai_full.mp4) · [`docs/demo/manual_full.mp4`](docs/demo/manual_full.mp4) · [`docs/demo/stats.json`](docs/demo/stats.json) · [`docs/demo/SPEC.md`](docs/demo/SPEC.md)

---

## Thesis Paper & Documentation

- **Thesis Paper (PDF):** [`docs/thesis/GLDtk_Thesis_Paper.pdf`](docs/thesis/GLDtk_Thesis_Paper.pdf) ([Markdown](docs/thesis/THESIS_PAPER.md) · [LaTeX](docs/thesis/latex/main.tex) · [Overleaf zip](docs/thesis/gldtk_thesis_overleaf.zip))
- **Ablation & Evaluation Report:** [`eval/results/ablations/report.md`](eval/results/ablations/report.md)
- **User Guide (install, prompts, sidebar walkthrough):** [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)
- **Technical Reference (API endpoints, providers, CLI, troubleshooting, Mac build):** [`docs/TECHNICAL_REFERENCE.md`](docs/TECHNICAL_REFERENCE.md)
- **Roadmap, Status & Literature:** [`docs/thesis/PROJECT_STATUS_AND_ROADMAP.md`](docs/thesis/PROJECT_STATUS_AND_ROADMAP.md) · [`docs/thesis/LITERATURE_AND_SOURCES.md`](docs/thesis/LITERATURE_AND_SOURCES.md)
