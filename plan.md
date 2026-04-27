# GLDtk × LDtk Native Integration

## Goal

Embed GLDtk as a panel **inside** the LDtk Electron window — no separate browser
tab. When the designer generates a level the project file is written and LDtk
reloads it automatically, all without leaving the editor.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  LDtk Electron window                                        │
│                                                              │
│  ┌─────────────────────────────┐  ┌───────────────────────┐ │
│  │  LDtk canvas (unchanged)    │  │  GLDtk panel (injected)│ │
│  │                             │  │  [chat history]       │ │
│  │  levels / tilesets / layers │  │  [Theme] [Enemies]    │ │
│  │                             │  │  [Difficulty] [Count] │ │
│  │  ← auto-reloads via IPC     │  │  [Prompt textarea]    │ │
│  │    when generation finishes │  │  [▶ Generate]         │ │
│  └─────────────────────────────┘  └───────────────────────┘ │
└─────────────────────────┬────────────────────────────────────┘
                          │ IPC reload + fetch POST /generate
                ┌─────────▼──────────┐
                │ GLDtk Python server │
                │ localhost:8765      │
                └────────────────────┘
```

## How the injection works

LDtk ships with `nodeIntegration: true, contextIsolation: false` — its renderer
has full Node.js access. We do **not** touch any Haxe source. Instead:

1. `patch.sh` clones LDtk, runs `npm install`, then makes two small edits:
   - Appends `<script src="gldtk-panel.js"></script>` to `app/assets/app.html`
   - Appends our IPC snippet to `app/assets/main.js`
2. `gldtk-panel.js` is copied into `app/assets/` — it creates the right-side panel.
3. On "Generate", the panel POSTs to the Python server, which writes the `.ldtk` file.
4. The panel sends `ipcRenderer.send('gldtk-reload')` → main process calls
   `mainWindow.webContents.reloadIgnoringCache()` → LDtk reloads and shows the
   new level (chat history survives via `localStorage`).

## Task list

- [x] 1. `ldtk-integration/patch.sh` — clone LDtk, inject files, launch
- [x] 2. `ldtk-integration/gldtk-panel.js` — full chat panel with generation + reload
- [x] 3. `ldtk-integration/gldtk-panel.css` — panel styles
- [x] 4. `ldtk-integration/reload-bridge.js` — IPC snippet appended to LDtk main.js
- [ ] 5. End-to-end test + README section

## Files created / modified

| File | Action |
|------|--------|
| `ldtk-integration/patch.sh` | NEW — setup & launch script |
| `ldtk-integration/gldtk-panel.js` | NEW — panel logic |
| `ldtk-integration/gldtk-panel.css` | NEW — panel styles |
| `ldtk-integration/reload-bridge.js` | NEW — IPC bridge |
| `plan.md` | UPDATED (this file) |
| `README.md` | TODO — add LDtk integration section |
