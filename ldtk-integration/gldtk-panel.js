/**
 * GLDtk Panel — injected into the LDtk Electron renderer.
 *
 * Runs entirely inside LDtk's window (nodeIntegration: true, no context
 * isolation), so it has full access to Node.js and Electron APIs.
 *
 * Flow:
 *   1. Injects the panel HTML + CSS into LDtk's document.
 *   2. Designer fills in a prompt and clicks Generate.
 *   3. Fetches POST http://localhost:8765/generate.
 *   4. Server writes the .ldtk file and returns stats + ASCII preview.
 *   5. Panel sends ipcRenderer.send("gldtk-reload") → main process reloads
 *      the Electron window → LDtk re-opens the updated project file.
 *   6. Chat history is preserved in localStorage across reloads.
 */

(function () {
  "use strict";

  // ── Constants ──────────────────────────────────────────────────────────
  const SERVER = "http://localhost:8765";
  const STORAGE_HISTORY = "gldtk_history";
  const STORAGE_OUTPUT  = "gldtk_output_path";
  const STORAGE_DIFF    = "gldtk_difficulty";

  // ── Guard: only inject once ────────────────────────────────────────────
  if (document.getElementById("gldtk-root")) return;

  // ── Electron IPC (available because nodeIntegration = true) ───────────
  let ipcRenderer = null;
  try {
    ipcRenderer = require("electron").ipcRenderer;
  } catch (_) {
    console.warn("[GLDtk] ipcRenderer unavailable — reload will be manual");
  }

  // ── State ──────────────────────────────────────────────────────────────
  let difficulty   = localStorage.getItem(STORAGE_DIFF) || "medium";
  let collapsed    = false;

  // ── Build DOM ──────────────────────────────────────────────────────────
  const link = document.createElement("link");
  link.rel   = "stylesheet";
  link.href  = "gldtk-panel.css";
  document.head.appendChild(link);

  const toggle = document.createElement("button");
  toggle.id        = "gldtk-toggle";
  toggle.title     = "Toggle GLDtk panel";
  toggle.textContent = "AI";
  document.body.appendChild(toggle);

  const root = document.createElement("div");
  root.id        = "gldtk-root";
  root.innerHTML = `
    <div class="g-header">
      <h2>GLDtk</h2>
      <span class="g-status" id="g-status">ready</span>
    </div>

    <div class="g-history" id="g-history"></div>

    <div class="g-controls">
      <div class="g-row">
        <span class="g-label">Output</span>
        <input type="text" id="g-output" style="flex:1;font-size:11px"
               placeholder="level.ldtk">
      </div>
      <div class="g-row">
        <span class="g-label">Theme</span>
        <select id="g-theme">
          <option value="">Auto</option>
          <option value="dungeon">Dungeon</option>
          <option value="forest">Forest</option>
          <option value="sky">Sky</option>
        </select>

        <span class="g-label">Platforms</span>
        <input type="number" id="g-platforms" min="2" max="20"
               style="width:46px" placeholder="—">
      </div>

      <div class="g-row">
        <span class="g-label">Enemies</span>
        <input type="text" id="g-enemies" style="flex:1"
               placeholder="Goblin, Slime">
      </div>

      <div class="g-row">
        <span class="g-label">Difficulty</span>
        <div class="g-diff">
          <button id="dEasy"   onclick="gldtk.setDiff('easy')">Easy</button>
          <button id="dMedium" onclick="gldtk.setDiff('medium')">Med</button>
          <button id="dHard"   onclick="gldtk.setDiff('hard')">Hard</button>
        </div>
      </div>

      <div class="g-prompt-row">
        <textarea id="g-prompt"
                  placeholder="A dungeon with a spike pit…"></textarea>
        <button class="g-btn" id="g-btn" onclick="gldtk.generate()">
          ▶ Gen
        </button>
      </div>
    </div>
  `;
  document.body.appendChild(root);

  // ── Restore persisted state ────────────────────────────────────────────
  const outInput = document.getElementById("g-output");
  outInput.value = localStorage.getItem(STORAGE_OUTPUT) || "level.ldtk";
  outInput.addEventListener("change", () =>
    localStorage.setItem(STORAGE_OUTPUT, outInput.value));

  setDiff(difficulty);
  restoreHistory();

  // ── Toggle collapse ────────────────────────────────────────────────────
  toggle.addEventListener("click", () => {
    collapsed = !collapsed;
    root.classList.toggle("collapsed", collapsed);
    toggle.textContent = collapsed ? "◀" : "AI";
  });

  // ── Helpers ────────────────────────────────────────────────────────────
  function setDiff(d) {
    difficulty = d;
    localStorage.setItem(STORAGE_DIFF, d);
    ["Easy", "Medium", "Hard"].forEach(x => {
      const el = document.getElementById("d" + x);
      if (el) el.classList.toggle("active", x.toLowerCase() === d);
    });
  }

  function parseList(raw) {
    if (!raw || !raw.trim()) return null;
    return raw.split(",").map(s => s.trim()).filter(Boolean);
  }

  function appendMsg(cls, html) {
    const hist = document.getElementById("g-history");
    if (!hist) return;
    const el = document.createElement("div");
    el.className = "g-msg " + cls;
    el.innerHTML = html;
    hist.appendChild(el);
    hist.scrollTop = hist.scrollHeight;
    persistHistory();
  }

  function persistHistory() {
    const hist = document.getElementById("g-history");
    if (hist) localStorage.setItem(STORAGE_HISTORY, hist.innerHTML);
  }

  function restoreHistory() {
    const saved = localStorage.getItem(STORAGE_HISTORY);
    const hist  = document.getElementById("g-history");
    if (saved && hist) {
      hist.innerHTML = saved;
      hist.scrollTop = hist.scrollHeight;
    }
  }

  function setStatus(text) {
    const el = document.getElementById("g-status");
    if (el) el.textContent = text;
  }

  // ── Reload LDtk ───────────────────────────────────────────────────────
  function reloadLDtk() {
    if (ipcRenderer) {
      // Preferred: tell the Electron main process to reload the window.
      // reload-bridge.js (appended to LDtk's main.js) handles this.
      ipcRenderer.send("gldtk-reload");
    } else {
      // Fallback: reload the renderer directly (loses panel state for 1 cycle
      // but chat history is in localStorage so it restores immediately).
      window.location.reload();
    }
  }

  // ── Main generate handler ──────────────────────────────────────────────
  async function generate() {
    const description = (document.getElementById("g-prompt").value || "").trim();
    if (!description) return;

    const btn  = document.getElementById("g-btn");
    btn.disabled = true;
    btn.innerHTML = '<span class="g-spinner"></span>…';
    setStatus("generating…");

    const theme    = document.getElementById("g-theme").value || null;
    const enemies  = parseList(document.getElementById("g-enemies").value);
    const platforms = parseInt(document.getElementById("g-platforms").value) || null;
    const outPath  = document.getElementById("g-output").value || "level.ldtk";

    // Show user bubble
    const meta = [
      theme ? `theme:${theme}` : null,
      enemies ? `enemies:${enemies.join("+")}` : null,
      platforms ? `platforms:${platforms}` : null,
      `difficulty:${difficulty}`,
    ].filter(Boolean).join(" · ");
    appendMsg("user",
      `<div class="g-tag">${meta}</div>${description}`
    );

    try {
      const resp = await fetch(`${SERVER}/generate`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          description,
          theme,
          enemy_types:    enemies,
          platform_count: platforms,
          difficulty,
          output_path:    outPath,
          seed:           Math.floor(Math.random() * 100000),
        }),
      });

      const data = await resp.json();

      if (!data.success) {
        appendMsg("err",
          `<div class="g-tag">Error</div>${data.error || "Unknown error"}`
        );
        setStatus("error");
      } else {
        const s  = data.stats || {};
        const et = Object.entries(s.entities || {})
          .map(([k, v]) => `${v}×${k}`).join(" ");
        const ascii = data.preview_ascii
          ? `<div class="g-ascii">${data.preview_ascii}</div>`
          : "";
        appendMsg("ai",
          `<div class="g-tag">Generated · ${s.theme || ""}</div>` +
          `<b>${s.nodes || 0}</b> nodes · <b>${s.edges || 0}</b> edges · ${et}` +
          ascii +
          `<div class="g-reload-hint">✓ Wrote ${data.output_path}<br>` +
          `Reloading LDtk…</div>`
        );
        setStatus("reloading…");
        // Give the browser a tick to paint the message before the reload.
        setTimeout(reloadLDtk, 400);
      }
    } catch (err) {
      appendMsg("err",
        `<div class="g-tag">Network error</div>` +
        `Is the GLDtk server running? (python server.py)<br>${err.message}`
      );
      setStatus("error");
    } finally {
      btn.disabled = false;
      btn.innerHTML = "▶ Gen";
    }
  }

  // ── Keyboard shortcut: Ctrl/Cmd + Enter in textarea ───────────────────
  document.getElementById("g-prompt").addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) generate();
  });

  // ── Expose to inline onclick handlers ─────────────────────────────────
  window.gldtk = { generate, setDiff };

})();
