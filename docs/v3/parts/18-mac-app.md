# Part 18: Packaged Mac app (build tooling)

Context: [../PLAN.md](../PLAN.md). Make a double-clickable **GLDtk.app**, plus a `.dmg`, that runs
LDtk with the sidebar **and** starts the backend itself, so no terminal, venv or Node is needed.
It is an unsigned local build for this Mac (Apple Silicon); no notarization.

## Backend bundle
- Use PyInstaller **one-folder** mode (start-up is faster than one-file) for `server.py`, with a
  spec file under `packaging/`. Include:
  - the Python packages;
  - `assets/` (themes, sprites, `manifest.json`, licences);
  - `spec` catalogs and any other data files the code reads with `__file__`-relative paths;
  - fastapi/uvicorn/pydantic hidden imports;
  - Pillow.
- Run a scan for every `Path(__file__)` and `open(` use, so nothing is missing at run time.
- **Configuration.**
  - The API key and fallback settings come from `~/Library/Application Support/GLDtk/.env`. The
    server must read it there, in addition to the repo `.env`.
  - On first launch, if there is no key, the sidebar shows a short "Add your Gemini API key"
    form that writes that file: a new `POST /settings` endpoint that sets `GEMINI_API_KEY` and
    `GLDTK_LOCAL_FALLBACK`. Keep the key out of logs and responses.
- **Smoke test:** add `--port` and `--check` flags to `server.py` (`--check` imports everything,
  loads the asset library and exits 0), so the bundle can be checked in CI-like fashion.

## Electron app (`ldtk-src`)
- **Packaging:** LDtk already packages with electron-builder (look at `app/package.json` /
  `electron-builder` config). Add the backend folder as `extraResources`.
- **Starting the backend.** The Electron main process (Haxe `main.hxml` sources, under
  `src/electron.main/`):
  - starts the bundled server on launch: `<resources>/gldtk-server/gldtk-server --port 8765`;
  - if port 8765 is taken, uses a free port and passes it to the renderer, and the sidebar
    reads it instead of the hard-coded 8765;
  - waits for the health endpoint;
  - kills the server on quit.

  In dev (`npm start`), keep today's behaviour: `start.sh` runs the Python server.
- **CSP:** update `connect-src` in `app/assets/app.html` if the port can change (allow
  `http://127.0.0.1:*` or the chosen port).
- **Branding:** app name **GLDtk**, with a distinct bundle id so it does not clash with a real
  LDtk install. Keep LDtk's licence and credits.

## Build script
`scripts/build_mac_app.sh` runs, in order:
1. the PyInstaller build;
2. the Haxe release builds (`renderer.hxml` and `main.hxml`, or the debug ones if the release
   flags break; explain which);
3. electron-builder `--mac dmg --arm64`.

It prints the path of the `.dmg`. Document it in the README ("Build the Mac app").

## Testing
- `gldtk-server --check` passes from the bundle.
- Launch the packaged app with Playwright, using the `.app`'s executable.
- With a fake key and a FakeProvider env switch (for example `GLDTK_FAKE_LLM=<fixture path>`,
  used only for tests), generate a level into a temp project and check that it renders.
- Quit, and check that no `gldtk-server` process is left.
- Check the `.dmg` mounts and contains `GLDtk.app`.
- Report the app size and the cold-start time.

## Rules
- Commits:
  - main repo (in a worktree; run `git reset --hard feat/v2` first): `v3 Part 18a: backend bundle
    and build script`;
  - `ldtk-src`: `v3 Part 18b: package GLDtk.app with the bundled backend`.
- Do not commit build outputs (`dist/`, `build/`, `.dmg`); add them to `.gitignore`.
- No push; do not edit PROGRESS.md; no real LLM.
- Port 8765 is yours for the duration. Stop every process you start.
- End each commit with the Co-Authored-By line.
