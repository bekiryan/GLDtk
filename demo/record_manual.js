// Manual side of the demo: rebuild the AI target level in LDtk's own editor
// (no GLDtk sidebar) with real mouse and keyboard events at human pace.
//
//   node demo/record_manual.js TARGET.ldtk [outDir] [--budget=300] [--tiles=simple|exact|none] [--region=x0,x1] [--no-record] [--estimate]
//
// Stops after --budget seconds of real time (default 300, 0 = build it all) and
// estimates the full build from every planned action. The plan (greedy rectangles per IntGrid value and per tile, entity list) comes
// from `make_projects.py plan`. --region limits the work to columns x0..x1 (dry runs).
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

/** Human timing model, in seconds (applied as real waits). */
const HUMAN = {
  click: 0.6,     // a click
  choose: 1.2,    // choosing a tool or layer, or switching panels
  perChar: 0.25,  // typing, per character (and per shortcut keystroke)
  think: 1.5,     // thinking before typing a field value
  drag: 1.0,      // a rectangle drag
  look: 2.0,      // "look at the reference", per section
};

/** Entity fields a designer sets by hand; the rest are GLDtk bookkeeping. */
const SKIP_FIELDS = new Set(['spec_id', 'section', 'anchor', 'gldtk_meta', 'sprite_tile', 'sprite', 'description']);

module.exports = { HUMAN };
if (require.main !== module) return;

const L = require('./lib');

async function main() {
  const args = process.argv.slice(2);
  const flag = n => args.find(a => a.startsWith('--' + n));
  const [targetArg, outArg] = args.filter(a => !a.startsWith('--'));
  const target = path.resolve(targetArg);
  const out = path.resolve(outArg || path.join(__dirname, 'out/manual'));
  const region = flag('region') ? flag('region').split('=')[1].split(',').map(Number) : null;
  // --tiles=simple (default: quick fills, see simple_tiles in make_projects.py) | exact | none
  const tilesMode = flag('no-tiles') ? 'none' : flag('tiles') ? flag('tiles').split('=')[1] : 'simple';
  const doTiles = tilesMode !== 'none';
  const record = !flag('no-record');
  const budget = flag('budget') ? Number(flag('budget').split('=')[1]) : 300;  // seconds; 0 = no limit
  fs.mkdirSync(out, { recursive: true });

  const py = path.join(L.ROOT, '.venv/bin/python'), mk = path.join(__dirname, 'make_projects.py');
  const file = path.join(out, 'crypt.ldtk'), planFile = path.join(out, 'plan.json');
  execFileSync(py, [mk, 'blank', target, file]);
  execFileSync(py, [mk, 'plan', target, planFile]);
  const assets = path.join(path.dirname(target), 'gldtk_assets');
  if (fs.existsSync(assets)) fs.cpSync(assets, path.join(out, 'gldtk_assets'), { recursive: true });
  const plan = JSON.parse(fs.readFileSync(planFile));
  const defs = JSON.parse(fs.readFileSync(target)).defs;
  const inRegion = r => !region || (r.x <= region[1] && r.x + (r.w || 1) - 1 >= region[0]);
  const sections = sectionBounds(plan);
  const full = estimateFull(plan, defs, sections, tilesMode);
  if (flag('estimate')) { console.log(JSON.stringify(full)); return; }  // modeled time only, no LDtk

  const { app, win, cdp } = await L.launch();
  await L.openProject(app, win, file);
  // The manual side is plain LDtk: hide the GLDtk sidebar.
  await win.evaluate(() => {
    const s = document.createElement('style');
    s.textContent = '#gldtk-root, #gldtk-toggle { display: none !important; }';
    document.head.appendChild(s);
  });
  await L.sleep(800);
  await L.showPointer(win);

  const rec = record ? new L.Recorder(cdp, out, { minInterval: 0.2, quality: 75 }) : null;
  const log = new L.ActionLog(path.join(out, 'actions.jsonl'));
  const me = new L.Human(win, log, HUMAN, { deadline: budget ? log.t0 + budget * 1000 : Infinity });
  // Progress on every log line: target cells painted, entities placed, share of the modeled work.
  const done = { coll: 0, tiles: 0, ents: 0 };
  const total = { coll: plan.collisions.reduce((n, r) => n + r.w * r.h, 0),
    tiles: tilesMode === 'simple' ? plan.simple_tiles.reduce((n, l) => n + l.fill_rects.length + l.top_runs.length, 0)
      : plan.tiles.reduce((n, l) => n + l.rects.reduce((m, r) => m + r.w * r.h, 0), 0), ents: plan.entities.length };
  log.extra = () => ({ coll: [done.coll, total.coll], tiles: [done.tiles, total.tiles], ents: [done.ents, total.ents],
    pct: +(100 * me.modeled / full.modeled_s).toFixed(1) });
  if (rec) await rec.start();
  await L.sleep(1500);
  const seen = new Set();
  const look = async x => {
    const s = sections.find(s => x >= s.x0 && x <= s.x1);
    if (s && !seen.has(s.id)) { seen.add(s.id); await me.look(1); }
  };

  let stopped = false;
  try {
    // 1. Level size
    log.phase(`Creating the ${plan.level.cWid}×${plan.level.cHei} level…`);
    await me.click('button.editLevelInstance', { kind: 'choose' });
    for (const [sel, v] of [['#width', plan.level.pxWid], ['#height', plan.level.pxHei]]) {
      await me.click(`.levelInstanceForm ${sel}, ${sel}`);
      await me.press('Meta+A');
      await me.type(String(v));
      await me.press('Enter');
    }
    await me.press('Escape');
    await L.sleep(800);
    await fitLevel(win, me);

    // 2. Collisions: big rock masses first, then the details, then hazards
    await me.click(layerSel('Collisions'), { kind: 'choose' });
    let curVal = null;
    for (const r of collisionOrder(plan.collisions.filter(inRegion))) {
      if (r.key !== curVal) {
        await me.click(`.palette li[data-id="${r.key}"], li.color[data-id="${r.key}"]`, { kind: 'choose' });
        curVal = r.key;
      }
      const big = r.w * r.h >= 6;
      log.phase(r.key !== 1 ? `Painting ${plan.intGridValues[r.key].toLowerCase()} cells…` : big ? 'Painting rock masses…' : 'Painting rock details…');
      await look(r.x);
      await paintRect(win, me, r);
      done.coll += r.w * r.h;
    }

    // 3. Tiles
    if (tilesMode === 'simple') for (const layer of plan.simple_tiles) {
      // A quick person: the fill variants in random mode over each rock mass, then the top surfaces.
      seen.clear();
      log.phase('Tiles: filling the rock…');
      await me.click(layerSel(layer.layer), { kind: 'choose' });
      if (!(await win.isVisible('button.toggle.on:has(.icon.fit)'))) await me.click('button.toggle:has(.icon.fit)');
      await me.drag(await tilePoint(win, layer.fill[0]), await tilePoint(win, layer.fill[layer.fill.length - 1]));
      await setRandom(win, me, true);
      for (const r of collisionOrder(layer.fill_rects.filter(inRegion))) {
        await look(r.x);
        await paintRect(win, me, r);
        done.tiles++;
      }
      log.phase('Tiles: top surfaces…');
      await pickTile(win, me, layer.top);
      await setRandom(win, me, false);
      for (const r of layer.top_runs.filter(inRegion)) {
        await paintRect(win, me, r);
        done.tiles++;
      }
    }
    if (tilesMode === 'exact') for (const layer of plan.tiles) {
      log.phase('Painting tiles…');
      seen.clear();
      await me.click(layerSel(layer.layer), { kind: 'choose' });
      if (!(await win.isVisible('button.toggle.on:has(.icon.fit)'))) await me.click('button.toggle:has(.icon.fit)');
      let flips = 0;
      for (const [k, rects] of tileGroups(layer.rects.filter(inRegion))) {
        const [tileId, f] = k.split(',').map(Number);
        await pickTile(win, me, tileId);
        if ((flips ^ f) & 1) await me.press('x');
        if ((flips ^ f) & 2) await me.press('y');
        flips = f;
        for (const r of rects) {
          await look(r.x);
          await paintRect(win, me, r);
          done.tiles += r.w * r.h;
        }
      }
    }

    // 4. Entities, once the structure is done: start, enemies, boss, items, exit
    const ents = entityOrder(plan.entities.filter(e => inRegion({ x: e.cx })));
    await me.click(layerSel('Entities'), { kind: 'choose' });
    seen.clear();
    for (const e of ents) {
      log.phase(entityCaption(e, ents));
      await look(e.cx);
      await me.click(`li.entity[data-defUid="${e.defUid}"]`, { kind: 'choose' });
      const at = await cellToPage(win, e.cx, e.cy);
      await uncover(win, me, at);
      await me.click(at, { note: e.id });
      await L.sleep(400);
      for (const f of await fieldsToSet(win, e)) {
        try { await setField(win, me, f); }
        catch (err) { if (err instanceof L.TimeUp) throw err; await win.screenshot({ path: path.join(out, 'error.png') }); throw err; }
      }
      await me.press('Escape');  // close the instance panel, it covers the level
      done.ents++;
    }

    // 5. Save
    log.phase('Saving…');
    await me.press('Escape');
    await me.press('Meta+S');
    await L.sleep(2500);
    log.phase('Done (not verified)');
  } catch (err) {
    if (!(err instanceof L.TimeUp)) throw err;
    stopped = true;
    log.phase(`Stopped at ${mmss(budget)} — ${(100 * me.modeled / full.modeled_s).toFixed(0)}% done`);
  }
  await L.sleep(1500);
  if (rec) await rec.stop();
  const elapsed = (Date.now() - log.t0) / 1000;
  if (stopped) {  // save what was built (not counted: the recording has ended) to measure it
    await win.keyboard.press('Escape').catch(() => {});
    await win.keyboard.press('Meta+S');
    await L.sleep(2500);
  }

  // Full-build estimate: the modeled time of every planned action, scaled by the measured
  // real/modeled ratio of the part that was done (mouse travel, LDtk redraws).
  const factor = elapsed / Math.max(me.modeled, 1);
  const res = { epoch: log.t0 / 1000, total_s: elapsed, counts: log.counts, steps: log.steps(), human: HUMAN,
    tiles: doTiles, tiles_mode: tilesMode,
    tile_fills: tilesMode === 'simple' ? plan.simple_tiles.reduce((n, l) => n + l.fill_rects.length + l.top_runs.length, 0) : null,
    region, budget_s: budget, stopped, progress: log.extra(),
    estimate: { ...full, real_per_modeled: +factor.toFixed(3), full_real_s: Math.round(full.modeled_s * factor) },
    compare: compare(target, file, region) };
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(res, null, 1));
  console.log(JSON.stringify(res));
  await app.close();
}

const mmss = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;

/** Solid cells first, biggest rectangles first (masses, then details); hazards last. */
function collisionOrder(rects) {
  return [...rects].sort((a, b) => (a.key === 1 ? 0 : a.key) - (b.key === 1 ? 0 : b.key)
    || (b.w * b.h >= 6) - (a.w * a.h >= 6) || a.x - b.x || a.y - b.y);
}

/** Tile rectangles grouped by (tile, flips), most used tile first, left to right. */
function tileGroups(rects) {
  const groups = new Map();
  for (const r of rects) {
    const k = r.key.join(',');
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  return [...groups].sort((a, b) => b[1].length - a[1].length)
    .map(([k, rs]) => [k, rs.sort((a, b) => a.x - b.x || a.y - b.y)]);
}

function entityOrder(ents) {
  const rank = e => e.id === 'Start' ? 0 : e.id === 'Exit' ? 9 : e.id === 'Enemy' ? (kindOf(e) === 'Boss' ? 2 : 1) : 3;
  return [...ents].sort((a, b) => rank(a) - rank(b) || a.cx - b.cx);
}

/** A field's default value from its definition (what LDtk shows on a new instance). */
function fieldDefault(fd) {
  const o = fd.defaultOverride;
  if (o && o.params && o.params.length) return String(o.params[0]);
  if (fd.canBeNull) return null;
  return { Int: '0', Float: '0', Bool: 'false', String: '' }[fd.__type] ?? null;
}

/**
 * Every action of the whole manual build with the same order and timing as
 * main(): counts per kind and the modeled seconds (before the real/modeled ratio).
 */
function estimateFull(plan, defs, sections, tilesMode) {
  const H = HUMAN, n = { click: 0, drag: 0, key: 0 };
  let t = 0;
  const click = (k = 'click') => { n.click++; t += k === 'choose' ? H.choose : H.click; };
  const drag = () => { n.drag++; t += H.drag; };
  const press = () => { n.key++; t += H.perChar; };
  const type = s => { n.key += s.length; t += H.think + s.length * H.perChar; };
  const looks = () => { t += H.look * sections.length; };
  const rect = r => (r.w === 1 && r.h === 1 ? click() : drag());
  const phases = {};
  const mark = (name, f) => { const t0 = t, c0 = { ...n }; f(); phases[name] = { s: +(t - t0).toFixed(1), click: n.click - c0.click, drag: n.drag - c0.drag, key: n.key - c0.key }; };

  mark('setup', () => {
    click('choose');
    for (const v of [plan.level.pxWid, plan.level.pxHei]) { click(); press(); type(String(v)); press(); }
    press(); n.drag++;  // the pan (wheel zoom is not counted)
  });
  mark('collisions', () => {
    click('choose');
    new Set(plan.collisions.map(r => r.key)).forEach(() => click('choose'));
    plan.collisions.forEach(rect); looks();
  });
  if (tilesMode === 'simple') mark('tiles', () => {
    for (const layer of plan.simple_tiles) {
      click('choose'); click(); drag(); click();       // layer, fit, picker drag, random on
      layer.fill_rects.forEach(rect); looks();
      click('choose'); click();                        // top tile, random off
      layer.top_runs.forEach(rect);
    }
  });
  if (tilesMode === 'exact') mark('tiles', () => {
    for (const layer of plan.tiles) {
      click('choose'); click();
      let flips = 0;
      for (const [k, rects] of tileGroups(layer.rects)) {
        const f = Number(k.split(',')[1]);
        click('choose');
        if ((flips ^ f) & 1) press();
        if ((flips ^ f) & 2) press();
        flips = f;
        rects.forEach(rect);
      }
      looks();
    }
  });
  mark('entities', () => {
    click('choose');
    for (const e of plan.entities) {
      const ed = defs.entities.find(d => d.uid === e.defUid);
      click('choose'); click();
      for (const f of e.fields) {
        const fd = ed && ed.fieldDefs.find(d => d.identifier === f.id);
        if (!fd || SKIP_FIELDS.has(f.id) || f.value === null || typeof f.value === 'object') continue;
        if (String(f.value) === String(fieldDefault(fd))) continue;
        if (f.type.startsWith('LocalEnum.')) { click(); click(); }
        else if (f.type === 'Bool') click();
        else { click(); press(); type(String(f.value)); press(); }
      }
      press();
    }
    looks();
  });
  mark('save', () => { press(); press(); });
  return { modeled_s: +t.toFixed(1), steps: n.click + n.drag + n.key, ...n, phases };
}

const layerSel = name => `#layers li:has(.name:text-is("${name}")), #layers li:has-text("${name}")`;

function sectionBounds(plan) {
  return (plan.sections || []).map(s => ({ id: s.id, x0: s.col_start, x1: s.col_end }));
}

function entityCaption(e, ents) {
  const same = ents.filter(x => x.id === e.id && kindOf(x) === kindOf(e)).length;
  const k = kindOf(e);
  return same > 1 ? `Placing ${same} ${(k || e.id).toLowerCase()}s…` : `Placing the ${(k || e.id).toLowerCase()}…`;
}
const kindOf = e => { const f = e.fields.find(f => f.type.startsWith('LocalEnum.') && f.id !== 'facing'); return f && f.value; };

/**
 * Escape until nothing covers the level at `p`: after typing a field value the first
 * Escape only leaves the text box, and the instance panel stays over the right side.
 */
async function uncover(win, me, p) {
  for (let i = 0; i < 3; i++) {
    const free = await win.evaluate(([x, y]) => { const el = document.elementFromPoint(x, y); return !!el && el.id === 'webgl'; }, p);
    if (free) return;
    await me.press('Escape');
    await L.sleep(250);
  }
  throw new Error('the level is covered at ' + p.map(Math.round));
}

/** Page coordinates of a level cell's centre, from LDtk's camera. */
async function cellToPage(win, cx, cy) {
  return win.evaluate(([cx, cy]) => {
    const E = __hx['page.Editor'].ME, c = E.camera, l = E.get_curLevel();
    const g = (o, k) => typeof o['get_' + k] === 'function' ? o['get_' + k]() : o[k];
    const grid = E.project.defaultGridSize;
    const cv = document.querySelector('canvas#webgl').getBoundingClientRect();
    const z = g(c, 'adjustedZoom') / g(c, 'pixelRatio');
    return [cv.left + (l.worldX + (cx + 0.5) * grid - g(c, 'left')) * z, cv.top + (l.worldY + (cy + 0.5) * grid - g(c, 'top')) * z];
  }, [cx, cy]);
}

async function paintRect(win, me, r) {
  const a = await cellToPage(win, r.x, r.y);
  if (r.w === 1 && r.h === 1) return me.click(a);
  const b = await cellToPage(win, r.x + r.w - 1, r.y + r.h - 1);
  await me.drag(a, b, { shift: true });
}

/** Wheel-zooms and middle-drag-pans until the level fills most of the canvas (as a person would). */
async function fitLevel(win, me) {
  for (let i = 0; i < 40; i++) {
    const cv = await win.evaluate(() => { const r = document.querySelector('canvas#webgl').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; });
    const { cWid, cHei } = await win.evaluate(() => { const l = __hx['page.Editor'].ME.get_curLevel(); return { cWid: l.pxWid / 16, cHei: l.pxHei / 16 }; });
    const a = await cellToPage(win, -0.5, -0.5), b = await cellToPage(win, cWid - 0.5, cHei - 0.5);
    const [x0, y0, w, h] = cv, lw = b[0] - a[0], lh = b[1] - a[1];
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], cmid = [x0 + w / 2, y0 + h / 2];
    if (lw > w * 0.94 || lh > h * 0.9) { await me.moveTo(cmid[0], cmid[1], 2); await win.mouse.wheel(0, 100); }
    else if (lw < w * 0.82 && lh < h * 0.8) { await me.moveTo(cmid[0], cmid[1], 2); await win.mouse.wheel(0, -100); }
    else if (Math.abs(mid[0] - cmid[0]) > 12 || Math.abs(mid[1] - cmid[1]) > 12) {
      await me.moveTo(mid[0], mid[1], 2);
      await win.mouse.down({ button: 'middle' });
      await win.mouse.move(cmid[0], cmid[1], { steps: 10 });
      await win.mouse.up({ button: 'middle' });
      me.log.add('drag', 1, { note: 'pan' });
    } else return;
    await L.sleep(300);
  }
}

/** Clicks a tile in the Tiles palette picker. */
async function pickTile(win, me, tileId) {
  await me.click(await tilePoint(win, tileId), { kind: 'choose' });
}

/** Turns the tile palette's random mode on or off with its button. */
async function setRandom(win, me, on) {
  const isOn = await win.isVisible('button.toggle.on:has(.icon.random)');
  if (isOn !== on) await me.click('button.toggle:has(.icon.random)');
}

/** Page point of a tile in the picker (page = wrapper + (local - scroll) * zoom). */
async function tilePoint(win, tileId) {
  return win.evaluate(id => {
    const E = __hx['page.Editor'].ME;
    const td = E.project.defs.getTilesetDef(E.get_curLayerDef().tilesetDefUid);
    const step = td.tileGridSize + td.spacing, cWid = Math.floor((td.pxWid - td.padding * 2 + td.spacing) / step);
    const lx = td.padding + (id % cWid) * step + td.tileGridSize / 2;
    const ly = td.padding + Math.floor(id / cWid) * step + td.tileGridSize / 2;
    const wrap = document.querySelector('.tileset'), atlas = wrap.querySelector('.wrapper'), cs = getComputedStyle(atlas);
    const z = parseFloat(cs.zoom) || 1, r = wrap.getBoundingClientRect();
    const sx = -parseFloat(cs.marginLeft) || 0, sy = -parseFloat(cs.marginTop) || 0;
    return [r.left + (lx - sx) * z, r.top + (ly - sy) * z];
  }, tileId);
}

/**
 * Tags each field row of the instance panel (the dd after its dt label) with
 * data-demo-field=<id> and returns the values shown. LDtk re-renders the form
 * after every change, so this runs before each field.
 */
function tagFields(win) {
  return win.evaluate(() => {
    const out = {};
    for (const lab of document.querySelectorAll('.entityInstanceEditor dt label')) {
      const id = lab.textContent.trim().replace(/\s.*$/, ''), dd = lab.closest('dt').nextElementSibling;
      if (!dd || dd.tagName !== 'DD') continue;
      dd.setAttribute('data-demo-field', id);
      const opt = dd.querySelector('.advancedSelect .option.selected'), inp = dd.querySelector('input, textarea');
      const v = opt ? opt.textContent : !inp ? null : inp.type === 'checkbox' ? String(inp.checked) : inp.value;
      if (v !== null) out[id] = v.replace(/ \(default\)$/, '');
    }
    return out;
  });
}

/** The entity's hand-set fields whose value differs from what LDtk shows. */
async function fieldsToSet(win, e) {
  const cur = await tagFields(win);
  return e.fields.filter(f => !SKIP_FIELDS.has(f.id) && f.value !== null && typeof f.value !== 'object'
    && f.id in cur && String(cur[f.id]).trim() !== String(f.value));
}

async function setField(win, me, f) {
  await tagFields(win);
  const dd = `.entityInstanceEditor dd[data-demo-field="${f.id}"]`;
  if (await win.locator(`${dd} .advancedSelect`).count()) {
    // LDtk shows enums as an "advanced select": it opens a picker, then click the value.
    await me.click(`${dd} .advancedSelect`);
    await win.waitForSelector('.selectPicker .value', { timeout: 3000 });
    await win.evaluate(v => {
      const el = [...document.querySelectorAll('.selectPicker .value')].find(e => e.textContent.trim() === v);
      if (el) el.setAttribute('data-demo-pick', '1');
    }, String(f.value));
    await me.click('.selectPicker .value[data-demo-pick]');
  } else if (await win.locator(`${dd} input[type=checkbox]`).count()) {
    await me.click(`${dd} input[type=checkbox]`);  // a Bool: one click
  } else {
    // A field at its default shows a placeholder: clicking its row opens the input.
    await me.click(dd);
    await win.waitForSelector(`${dd} input:visible, ${dd} textarea:visible`, { timeout: 3000 }).catch(() => {});
    // Only type into a focused text box: elsewhere letters are LDtk shortcuts (e = entity panel).
    if (!(await win.evaluate(() => /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName))))
      throw new Error(`no text box focused for field ${f.id}`);
    await me.press('Meta+A');
    await me.type(String(f.value));
    await me.press('Enter');
  }
  await L.sleep(250);
}

function compare(target, file, region) {
  const py = path.join(L.ROOT, '.venv/bin/python');
  const out = execFileSync(py, [path.join(__dirname, 'compare.py'), target, file, ...(region ? ['--region', region.join(',')] : [])]);
  return JSON.parse(out);
}

main().catch(e => { console.error(e); process.exit(1); });
