// AI side of the demo: type the prompt in the GLDtk sidebar, Generate, then
// one section edit ("Make this section a arena") and its undo. Real LLM.
//
//   node demo/record_ai.js [outDir]       (default demo/out/ai; needs server.py on :8765)
//
// Writes outDir/{crypt.ldtk, target.ldtk, actions.jsonl, frames/, frames.json, result.json}.
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const L = require('./lib');
const { HUMAN } = require('./record_manual');

const PROMPT = 'A crypt: a long corridor with three skeletons, then a small labyrinth of catacombs '
  + 'with a treasure chest in a dead end, and a boss arena where the lich waits by the exit.';
const MODEL = process.env.DEMO_MODEL || 'gemini-3.1-flash-lite';
const EDIT_TO = 'arena';

async function main() {
  const out = path.resolve(process.argv[2] || path.join(__dirname, 'out/ai'));
  fs.mkdirSync(out, { recursive: true });
  const file = path.join(out, 'crypt.ldtk');
  execFileSync(path.join(L.ROOT, '.venv/bin/python'), [path.join(__dirname, 'make_projects.py'), 'empty', file]);

  const { app, win, cdp } = await L.launch();
  // Setup, not counted: provider + model, empty chat, the empty project.
  await win.click('#g-clear').catch(() => {});
  await win.selectOption('#g-provider', 'gemini');
  await win.fill('#g-model', MODEL);
  await win.locator('#g-model').dispatchEvent('change');
  await L.openProject(app, win, file);
  await L.showPointer(win);

  const rec = new L.Recorder(cdp, out);
  const log = new L.ActionLog(path.join(out, 'actions.jsonl'));
  const me = new L.Human(win, log, HUMAN);
  const res = { model: MODEL, prompt: PROMPT };
  await rec.start();
  await L.sleep(1500);

  log.phase('Typing the prompt…');
  await me.click('#g-prompt');
  await me.type(PROMPT);
  res.prompt_words = PROMPT.split(/\s+/).length;

  log.phase('Generating…');
  const genResp = win.waitForResponse(r => r.url().includes('/generate'), { timeout: 900000 });
  await me.click('#g-btn');
  const tGen = Date.now();
  const gen = await (await genResp).json();
  await waitLoaded(win, file);
  res.generate_s = (Date.now() - tGen) / 1000;
  res.verified = gen.verified === true;
  res.success = gen.success;
  res.warnings = gen.warnings || [];
  const fb = res.warnings.find(w => /answered by/.test(w));
  res.answered_by = fb ? fb.replace(/^.*answered by\s*/, '') : `gemini (${MODEL})`;
  res.stats = gen.stats; res.sections = gen.sections;
  log.write('generated', { generate_s: res.generate_s, verified: res.verified, answered_by: res.answered_by });
  fs.copyFileSync(file, path.join(out, 'target.ldtk'));
  res.done_t = (Date.now() - log.t0) / 1000;
  await me.look(1);

  // Follow-up edit on one section, then undo it.
  log.phase(`Edit: "Make this section a ${EDIT_TO}"`);
  if (!(await win.isVisible('#g-edit'))) await me.click('#g-edit-head');
  await me.click('.g-mode-btn[data-mode=section]', { kind: 'choose' });
  const opts = await win.evaluate(() => [...document.querySelectorAll('#g-section option')].map(o => [o.value, o.textContent]));
  const pick = opts.find(o => /labyrinth|catacomb/i.test(o[1])) || opts[1] || opts[0];
  await me.click('#g-section'); await win.selectOption('#g-section', pick[0]); log.add('click');
  res.edited_section = pick[1];
  const editResp = win.waitForResponse(r => r.url().includes('/edit/section'), { timeout: 900000 });
  await me.click('#g-structure'); await win.selectOption('#g-structure', EDIT_TO); log.add('click');
  const tEdit = Date.now();
  const ed = await (await editResp).json();
  await L.sleep(600);
  res.edit_s = (Date.now() - tEdit) / 1000;
  res.edit_success = ed.success; res.edit_verified = ed.verified === true;
  log.write('edited', { edit_s: res.edit_s, verified: res.edit_verified });
  await me.look(1);

  log.phase('Undo the edit');
  const undoResp = win.waitForResponse(r => r.url().includes('/undo'), { timeout: 120000 });
  await me.click('#g-undo');
  const tUndo = Date.now();
  const un = await (await undoResp).json();
  await L.sleep(600);
  res.undo_s = (Date.now() - tUndo) / 1000;
  res.undo_success = un.success !== false;
  log.write('undone', { undo_s: res.undo_s });
  await me.look(1);
  log.phase('Done');
  await L.sleep(1000);

  await rec.stop();
  res.total_s = (Date.now() - log.t0) / 1000;
  res.counts = log.counts; res.steps = log.steps(); res.epoch = log.t0 / 1000;
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(res, null, 1));
  console.log(JSON.stringify({ ...res, warnings: res.warnings.slice(0, 3), stats: undefined }));
  await app.close();
}

/** Waits for the sidebar to finish and LDtk to reload the written file. */
async function waitLoaded(win, file) {
  for (let i = 0; i < 1200; i++) {
    const busy = await win.evaluate(() => /generating/i.test(document.querySelector('#g-btn').innerText + document.querySelector('#g-status').innerText));
    if (!busy) break;
    await L.sleep(250);
  }
  await L.sleep(1200);  // the sidebar reloads the project after 400 ms
}

main().catch(e => { console.error(e); process.exit(1); });
