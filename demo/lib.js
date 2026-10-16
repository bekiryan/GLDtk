// Shared helpers for the demo recorders: launch LDtk, reach its Haxe classes,
// record the window with CDP screencast, and log user actions.
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const APP_DIR = path.join(ROOT, 'ldtk-src/app');
const ELECTRON = path.join(APP_DIR, 'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron');
const sleep = ms => new Promise(r => setTimeout(r, ms));

function playwright() {
  if (process.env.PLAYWRIGHT) return require(process.env.PLAYWRIGHT);
  try { return require('playwright'); } catch (e) {}
  // npx cache fallback (no playwright in the repo)
  const base = path.join(require('os').homedir(), '.npm/_npx');
  for (const d of fs.existsSync(base) ? fs.readdirSync(base) : []) {
    const p = path.join(base, d, 'node_modules/playwright');
    if (fs.existsSync(p)) return require(p);
  }
  throw new Error('playwright not found: set PLAYWRIGHT=/path/to/node_modules/playwright');
}

/** Launches LDtk and waits for the home screen. Returns {app, win, cdp}. */
async function launch({ width = 1600, height = 900 } = {}) {
  const app = await playwright()._electron.launch({ executablePath: ELECTRON, args: ['.'], cwd: APP_DIR });
  let win = null;
  for (let i = 0; i < 60 && !win; i++) {
    await sleep(500);
    for (const w of app.windows()) { try { if (await w.$('button.load')) win = w; } catch (e) {} }
  }
  if (!win) throw new Error('LDtk window not found');
  await app.evaluate(({ BrowserWindow }, [w, h]) => {
    const b = BrowserWindow.getAllWindows()[0]; b.unmaximize(); b.setSize(w, h); b.center();
  }, [width, height]);
  await sleep(800);
  const cdp = await win.context().newCDPSession(win);
  await exposeHaxe(cdp);
  return { app, win, cdp };
}

/**
 * The Haxe classes live inside the renderer's closure. Find a closure-scoped
 * function, walk its [[Scopes]] and expose the class registry ($hxClasses) as
 * window.__hx (by dotted name: __hx.App, __hx['page.Editor'], ...).
 */
async function exposeHaxe(cdp) {
  const { result } = await cdp.send('Runtime.evaluate', {
    expression: `(() => { const ev = $._data(document, 'events') || {}; for (const k in ev) for (const h of ev[k]) return h.handler; return null; })()`,
  });
  if (!result.objectId) throw new Error('no closure handler found');
  let objectId = result.objectId, scopes = null;
  for (let i = 0; i < 4 && !scopes; i++) {  // Haxe binds its handlers: follow [[TargetFunction]]
    const internal = (await cdp.send('Runtime.getProperties', { objectId, ownProperties: false })).internalProperties || [];
    scopes = internal.find(p => p.name === '[[Scopes]]');
    const target = internal.find(p => p.name === '[[TargetFunction]]');
    if (!scopes && target) objectId = target.value.objectId;
  }
  if (!scopes) throw new Error('no [[Scopes]] on the handler');
  const list = await cdp.send('Runtime.getProperties', { objectId: scopes.value.objectId, ownProperties: true });
  // The renderer closure holds $hxClasses, the registry of every class by its dotted name.
  for (const sc of list.result) {
    const vars = await cdp.send('Runtime.getProperties', { objectId: sc.value.objectId, ownProperties: true });
    const reg = vars.result.find(v => v.name === '$hxClasses');
    if (reg) await cdp.send('Runtime.callFunctionOn', {
      objectId: reg.value.objectId, functionDeclaration: 'function(){ window.__hx = this; }',
    });
  }
  const ok = (await cdp.send('Runtime.evaluate', { expression: '!!(window.__hx && window.__hx.App && window.__hx["page.Editor"])', returnByValue: true })).result.value;
  if (!ok) throw new Error('could not reach the Haxe classes');
}

/** Opens a .ldtk through the home screen's Load button (the dialog is stubbed). */
async function openProject(app, win, file) {
  await app.evaluate(({ dialog }, p) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [p] });
    dialog.showOpenDialogSync = () => [p];
  }, file);
  await win.click('button.load');
  for (let i = 0; i < 40; i++) {
    await sleep(250);
    if (await win.evaluate(() => { const E = window.__hx['page.Editor']; return !!(E && E.ME && E.ME.project); })) break;
  }
  await sleep(1000);
}

/** Records CDP screencast frames (jpeg + timestamp) into dir/frames. */
class Recorder {
  /** `minInterval` (s): keep at most one frame per interval (long runs). */
  constructor(cdp, dir, { minInterval = 0, quality = 85 } = {}) {
    this.cdp = cdp; this.dir = path.join(dir, 'frames'); this.frames = [];
    this.minInterval = minInterval; this.quality = quality; this.last = 0;
    fs.rmSync(this.dir, { recursive: true, force: true }); fs.mkdirSync(this.dir, { recursive: true });
    this.onFrame = async f => {
      const t = Date.now() / 1000;
      if (t - this.last < this.minInterval) {
        try { await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }); } catch (e) {}
        return;
      }
      this.last = t;
      const name = `f${String(this.frames.length).padStart(6, '0')}.jpg`;
      fs.writeFileSync(path.join(this.dir, name), Buffer.from(f.data, 'base64'));
      this.frames.push({ t, file: name });
      try { await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }); } catch (e) {}
    };
  }
  async start() {
    this.cdp.on('Page.screencastFrame', this.onFrame);
    await this.cdp.send('Page.startScreencast', { format: 'jpeg', quality: this.quality, everyNthFrame: 1 });
  }
  async stop() {
    try { await this.cdp.send('Page.stopScreencast'); } catch (e) {}
    this.cdp.off('Page.screencastFrame', this.onFrame);
    fs.writeFileSync(path.join(path.dirname(this.dir), 'frames.json'), JSON.stringify(this.frames));
  }
}

/** JSONL action log: {t (s since start), action, count, ...}. */
class ActionLog {
  constructor(file) {
    this.file = file; this.t0 = Date.now(); this.counts = { click: 0, drag: 0, key: 0 };
    this.extra = () => ({});  // progress fields added to every line
    fs.writeFileSync(file, '');
  }
  write(action, extra = {}) {
    const t = (Date.now() - this.t0) / 1000;
    fs.appendFileSync(this.file, JSON.stringify({ t: +t.toFixed(3), action, count: this.steps(), ...this.counts, ...this.extra(), ...extra }) + '\n');
  }
  add(kind, n = 1, extra = {}) { this.counts[kind] += n; this.write(kind, extra); }
  phase(caption) { this.write('phase', { caption }); }
  steps() { return this.counts.click + this.counts.drag + this.counts.key; }
}

/** Thrown by Human when the time budget is used up. */
class TimeUp extends Error {}

/** A visible pointer for the recording (the screencast has no OS cursor). */
async function showPointer(win) {
  await win.evaluate(() => {
    if (document.getElementById('demo-pointer')) return;
    const p = document.createElement('div');
    p.id = 'demo-pointer';
    p.style.cssText = 'position:fixed;left:-50px;top:-50px;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;'
      + 'border:3px solid #ffd400;box-shadow:0 0 0 2px #000;pointer-events:none;z-index:2147483647;transition:transform .08s';
    document.body.appendChild(p);
    const at = e => { p.style.left = e.clientX + 'px'; p.style.top = e.clientY + 'px'; };
    window.addEventListener('mousemove', at, true);
    window.addEventListener('mousedown', e => { at(e); p.style.transform = 'scale(.6)'; }, true);
    window.addEventListener('mouseup', () => { p.style.transform = ''; }, true);
  });
}

/**
 * User actions at human pace: every action waits as long as a person would
 * take (timing from `human`), then is counted in the action log.
 */
class Human {
  constructor(win, log, human, { deadline = Infinity } = {}) {
    this.win = win; this.log = log; this.h = human; this.pos = [800, 450];
    this.deadline = deadline;  // epoch ms: the next action after it throws TimeUp
    this.modeled = 0;          // modeled seconds of the actions done so far
  }
  wait(s) { return sleep(s * 1000); }
  /** Called before every action: stops the run at the deadline, adds the action's modeled time. */
  begin(seconds) {
    if (Date.now() >= this.deadline) throw new TimeUp();
    this.modeled += seconds;
  }
  async moveTo(x, y, steps = 6) { await this.win.mouse.move(x, y, { steps }); this.pos = [x, y]; }
  /** Clicks a selector or a point. `kind`: 'click' (0.6 s) or 'choose' (tool/layer/panel, 1.2 s). */
  async click(target, { kind = 'click', note } = {}) {
    this.begin(kind === 'choose' ? this.h.choose : this.h.click);
    let [x, y] = Array.isArray(target) ? target : await this.center(target);
    await this.moveTo(x, y);
    await this.wait(kind === 'choose' ? this.h.choose : this.h.click);
    await this.win.mouse.down(); await sleep(60); await this.win.mouse.up();
    this.log.add('click', 1, note ? { note } : {});
  }
  async center(sel) {
    const el = this.win.locator(sel).first();
    await el.scrollIntoViewIfNeeded();
    const b = await el.boundingBox();
    if (!b) throw new Error('not visible: ' + sel);
    return [b.x + b.width / 2, b.y + b.height / 2];
  }
  /** A rectangle drag between two points; `shift` holds Shift (LDtk's rectangle mode). */
  async drag(a, b, { shift = false, note } = {}) {
    this.begin(this.h.drag);
    // LDtk reads the mouse once per frame: settle before pressing and before releasing.
    const t = this.h.drag * 1000, settle = 120, n = Math.max(4, Math.round((t - 3 * settle) / 50));
    await this.moveTo(a[0], a[1], 3);
    if (shift) await this.win.keyboard.down('Shift');
    await sleep(settle);
    await this.win.mouse.down();
    await sleep(settle);
    for (let i = 1; i <= n; i++) {
      await this.win.mouse.move(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n);
      await sleep((t - 3 * settle) / n);
    }
    await sleep(settle);
    await this.win.mouse.up();
    if (shift) await this.win.keyboard.up('Shift');
    this.pos = b;
    this.log.add('drag', 1, note ? { note } : {});
  }
  /** Types text: thinks first, then one keystroke per character. */
  async type(text, { think = true } = {}) {
    this.begin((think ? this.h.think : 0) + text.length * this.h.perChar);
    if (think) await this.wait(this.h.think);
    for (const ch of text) {
      await this.win.keyboard.type(ch);
      await this.wait(this.h.perChar);
      this.log.add('key');
    }
  }
  /** A key or shortcut (e.g. 'Enter', 'Meta+A', 'Meta+S') counted as one keystroke. */
  async press(key) {
    this.begin(this.h.perChar);
    await this.wait(this.h.perChar);
    await this.win.keyboard.press(key);
    this.log.add('key');
  }
  async look(sections = 1) { this.begin(this.h.look * sections); await this.wait(this.h.look * sections); }
}

module.exports = { ROOT, sleep, launch, openProject, Recorder, ActionLog, Human, TimeUp, showPointer };
