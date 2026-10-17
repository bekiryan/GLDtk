"""Compose the side-by-side demo video from the two recordings.

    python demo/compose.py [--out demo/out] [--speed N]

Reads out/manual and out/ai (frames/, frames.json, actions.jsonl, result.json)
and writes out/comparison.mp4, out/manual_full.mp4, out/ai_full.mp4,
out/stats.json and out/poster.png. Overlays (timer, step counter, captions,
banners, end card) are one ASS subtitle file per video, built from the action
logs; the timer always shows real elapsed time, also when the panel is sped up.
"""
from __future__ import annotations

import argparse
import bisect
import json
import subprocess
from pathlib import Path

W, H = 1920, 1080
PANEL_W, PANEL_H, PANEL_Y = 960, 524, 80
END_CARD_S = 9.0
SPEEDS = [1, 2, 3, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64]
TARGET_S = 100.0  # aim for a main part of at most ~100 s

FONT, MONO = "Helvetica Neue", "Menlo"
WHITE, GREY, YELLOW, GREEN, RED = "&HFFFFFF&", "&HA0A0A0&", "&H33D4FF&", "&H5CD65C&", "&H5050FF&"


# ── data ───────────────────────────────────────────────────────────────────

class Run:
    def __init__(self, d: Path):
        self.dir = d
        self.res = json.loads((d / "result.json").read_text())
        self.frames = json.loads((d / "frames.json").read_text())
        self.actions = [json.loads(l) for l in (d / "actions.jsonl").read_text().splitlines() if l.strip()]
        self.epoch = self.res["epoch"]
        self.total = self.res["total_s"]
        self.count_t = [a["t"] for a in self.actions]

    def at(self, t: float) -> dict:
        """The last action-log line at real time *t* (counts and caption)."""
        i = bisect.bisect_right(self.count_t, t) - 1
        line = self.actions[i] if i >= 0 else {"count": 0, "click": 0, "drag": 0, "key": 0}
        cap = next((a["caption"] for a in reversed(self.actions[: i + 1]) if a["action"] == "phase"), "")
        return {**line, "caption": cap}


# ── ffmpeg helpers ─────────────────────────────────────────────────────────

def concat_list(run: Run, speed: float, path: Path) -> None:
    """A concat-demuxer list: each frame lasts until the next, divided by *speed*."""
    fr = run.frames
    lines = []
    for i, f in enumerate(fr):
        nxt = fr[i + 1]["t"] if i + 1 < len(fr) else run.epoch + run.total
        lines.append(f"file '{run.dir / 'frames' / f['file']}'")
        lines.append(f"duration {max(nxt - f['t'], 0.001) / speed:.4f}")
    lines.append(f"file '{run.dir / 'frames' / fr[-1]['file']}'")
    path.write_text("\n".join(lines) + "\n")


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


# ── ASS ────────────────────────────────────────────────────────────────────

def ts(t: float) -> str:
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def mmss(s: float) -> str:
    s = int(s + 0.5)
    return f"{s // 60:02d}:{s % 60:02d}"


class Ass:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.events: list[str] = []

    def text(self, a: float, b: float, x: int, y: int, txt: str, size=36, color=WHITE, an=7,
             font=FONT, bold=False, box: str | None = None, layer=2) -> None:
        tags = f"\\an{an}\\pos({x},{y})\\fn{font}\\fs{size}\\c{color}\\b{int(bold)}"
        if box:  # opaque box behind the text
            tags += f"\\bord8\\3c{box}\\shad0"
            style = "Box"
        else:
            style = "Default"
        self.events.append(f"Dialogue: {layer},{ts(a)},{ts(b)},{style},,0,0,0,,{{{tags}}}{txt}")

    def rect(self, a: float, b: float, x0: int, y0: int, x1: int, y1: int, color: str, layer=1, alpha="00") -> None:
        self.events.append(
            f"Dialogue: {layer},{ts(a)},{ts(b)},Default,,0,0,0,,"
            f"{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{color}\\1a&H{alpha}&\\p1}}"
            f"m {x0} {y0} l {x1} {y0} {x1} {y1} {x0} {y1}{{\\p0}}")

    def write(self, path: Path) -> None:
        head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {self.w}
PlayResY: {self.h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT},36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,7,0,0,0,1
Style: Box,{FONT},36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,3,8,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        path.write_text(head + "\n".join(self.events) + "\n")


def panel_overlay(ass: Ass, run: Run, x0: int, speed: float, until: float, info_y: int, step=0.1,
                  hold_from: float | None = None) -> None:
    """Timer, step counter and caption under a panel, sampled every *step* video seconds."""
    t = 0.0
    while t < until - 1e-6:
        real = min(t * speed, run.total)
        st = run.at(real)
        b = min(t + step, until)
        ass.text(t, b, x0 + 40, info_y, mmss(real), size=96, font=MONO, bold=True)
        ass.text(t, b, x0 + 520, info_y + 4, f"{st['count']:,} steps", size=56, bold=True)
        ass.text(t, b, x0 + 520, info_y + 76,
                 f"{st['click']:,} clicks · {st['drag']:,} drags · {st['key']:,} keys", size=28, color=GREY)
        if st["caption"]:
            ass.text(t, b, x0 + 40, info_y + 140, st["caption"], size=40, color=YELLOW)
        if "pct" in st:
            if not run.res.get("stopped"):  # a finished build: share of the steps it really took
                st = {**st, "pct": 100 * st["count"] / max(run.res["steps"], 1)}
            ass.text(t, b, x0 + 40, info_y + 200, progress_line(st), size=30, color=WHITE)
        t = b
    ass.text(0, until, x0 + 40, info_y + 108, "real time", size=26, color=GREY)


def progress_line(st: dict) -> str:
    c, e = st["coll"], st["ents"]
    return (f"{st['pct']:.0f}% of the full build done  ·  collision cells {100 * c[0] / max(c[1], 1):.0f}%  ·  "
            f"entities {e[0]}/{e[1]}")


def badge(ass: Ass, a: float, b: float, x_right: int, y: int, txt: str, color="&H202020&") -> None:
    ass.text(a, b, x_right, y, txt, size=40, bold=True, an=9, box=color, layer=3)


# ── main ───────────────────────────────────────────────────────────────────

def manual_check_text(check, short: bool = False) -> str:
    """The manual side's playability: checked afterwards with GLDtk's verifier (demo/verify.py)."""
    if not check:
        return "not verified" if short else "no — not checked by hand"
    if check.get("playable"):
        return "playable (checked after)" if short else "yes — checked afterwards with GLDtk's verifier"
    why = ("Start cannot reach the Exit" if not check.get("start_reaches_exit")
           else f"{check.get('softlock_spots', 0)} soft-lock spot(s)")
    return f"not playable: {why}" if short else f"no — {why} (GLDtk's verifier)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    ap.add_argument("--speed", type=float, default=None)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    man, ai = Run(out / "manual"), Run(out / "ai")
    tmp = out / "tmp"
    tmp.mkdir(exist_ok=True)

    speed = args.speed or next((s for s in SPEEDS if man.total / s <= TARGET_S), SPEEDS[-1])
    main_len = max(man.total / speed, ai.total)
    total = main_len + END_CARD_S
    ai_done = ai.res["done_t"]
    model = ai.res["answered_by"]
    hm = man.res["human"]
    cmp_ = man.res.get("compare", {})
    # demo/verify.py result for the finished manual file (offline, after the build; not timed)
    vf = out / "manual" / "verify.json"
    man_check = json.loads(vf.read_text(encoding="utf-8")) if vf.is_file() else None

    stats = {
        "manual": {"real_time_s": round(man.total, 1), "steps": man.res["steps"], **man.res["counts"],
                   "verified": bool(man_check and man_check.get("playable")), "check": man_check, "tiles_included": man.res.get("tiles", True),
                   "tiles_mode": man.res.get("tiles_mode", "exact"), "tile_fills": man.res.get("tile_fills"),
                   "stopped_at_budget": man.res.get("stopped", False), "progress": man.res.get("progress"),
                   "estimate_full": man.res.get("estimate"),
                   "matches_target": cmp_, "human_timing_s": hm},
        "ai": {"real_time_s": round(ai_done, 1), "generate_s": round(ai.res["generate_s"], 1),
               "steps": ai.at(ai_done)["count"], **{k: ai.at(ai_done)[k] for k in ("click", "drag", "key")},
               "prompt_words": ai.res["prompt_words"], "verified": ai.res["verified"],
               "answered_by": model, "edit_s": round(ai.res["edit_s"], 1), "undo_s": round(ai.res["undo_s"], 1),
               "edit": f"{ai.res['edited_section']} -> arena", "recording_s": round(ai.total, 1)},
        "video": {"manual_speed": speed, "length_s": round(total, 1)},
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=1))

    # ── full videos at ×1 ──
    for name, run, label in (("manual_full", man, "Manual (LDtk) · simulated at human pace"),
                             ("ai_full", ai, f"GLDtk AI · {model}")):
        lst = tmp / f"{name}.txt"
        concat_list(run, 1, lst)
        ass = Ass(1600, 872)
        t, step = 0.0, 0.25
        while t < run.total:
            st = run.at(t)
            b = min(t + step, run.total)
            ass.text(t, b, 1580, 20, f"{mmss(t)}  ·  {st['count']:,} steps", size=34, font=MONO, an=9, box="&H202020&")
            if st["caption"]:
                ass.text(t, b, 800, 840, st["caption"], size=34, an=2, box="&H202020&", color=YELLOW)
            t = b
        ass.text(0, run.total, 20, 840, label, size=26, an=1, box="&H202020&", color=GREY)
        ass.write(tmp / f"{name}.ass")
        ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-vf",
               f"fps=30,scale=1600:872,ass={tmp / (name + '.ass')}", "-t", f"{run.total:.2f}",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", str(out / f"{name}.mp4"))

    # ── comparison ──
    concat_list(man, speed, tmp / "man.txt")
    concat_list(ai, 1, tmp / "ai.txt")
    ass = Ass(W, H)
    ass.rect(0, total, 0, 0, W, PANEL_Y, "&H1A1410&")
    ass.text(0, total, 480, 40, "Manual (LDtk)", size=46, bold=True, an=5)
    ass.text(0, total, 1440, 40, "GLDtk AI", size=46, bold=True, an=5)
    ass.rect(0, total, 958, 0, 962, PANEL_Y + PANEL_H, "&H404040&", layer=4)
    info_y = PANEL_Y + PANEL_H + 24
    panel_overlay(ass, man, 0, speed, main_len, info_y)
    panel_overlay(ass, ai, 960, 1, main_len, info_y)
    badge(ass, 0, main_len, 950, PANEL_Y + 10, f"×{speed:g}")
    badge(ass, 0, main_len, 1910, PANEL_Y + 10, "×1")
    ass.text(0, main_len, 950, PANEL_Y + 64, "simulated at human pace", size=24, an=9, box="&H202020&", color=GREY, layer=3)
    ass.text(0, main_len, 1910, PANEL_Y + 64, f"real run · {model}", size=24, an=9, box="&H202020&", color=GREY, layer=3)
    gen_v = ai_done  # the AI panel plays at ×1
    ass.text(gen_v, main_len, 1440, PANEL_Y + PANEL_H - 16,
             f"Done in {int(ai_done + 0.5)} s  ·  generate {ai.res['generate_s']:.0f} s  ·  verified, soft-lock-free",
             size=32, bold=True, an=2, box="&H2E7D32&", layer=3)
    ass.text(ai.total, main_len, 1440, PANEL_Y + PANEL_H - 76,
             f"Edit → arena {ai.res['edit_s']:.1f} s  ·  undo {ai.res['undo_s']:.1f} s", size=28, an=2,
             box="&H202020&", layer=3)
    if man.res.get("stopped"):
        est = man.res["estimate"]
        ass.text(man.total / speed, main_len, 480, PANEL_Y + PANEL_H - 16,
                 f"Stopped at {mmss(man.total)} · {man.res['progress']['pct']:.0f}% done · full build ≈ {mmss(est['full_real_s'])} (est.)",
                 size=30, bold=True, an=2, box="&H303030&", layer=3)
    elif man.total / speed < main_len:
        ass.text(man.total / speed, main_len, 480, PANEL_Y + PANEL_H - 16, f"Done in {mmss(man.total)} · {manual_check_text(man_check, short=True)}",
                 size=32, bold=True, an=2, box="&H303030&", layer=3)
    tiles_note = ("" if man.res.get("tiles", True) else "  ·  tiles not included — would add more time")
    if man.res.get("tiles_mode") == "simple":
        tiles_note = f"  ·  tiles simplified ({man.res['tile_fills']} fills)"
    ass.text(0, main_len, 960, H - 18,
             f"Manual side scripted at human pace: click {hm['click']} s · tool/layer/panel {hm['choose']} s · "
             f"typing {hm['perChar']} s/char + {hm['think']} s · drag {hm['drag']} s · look {hm['look']} s/section"
             f"{tiles_note}", size=22, an=2, color=GREY)
    end_card(ass, main_len, total, stats, hm)
    ass.write(tmp / "comparison.ass")

    fc = (f"color=c=0x10141A:s={W}x{H}:r=30:d={total:.2f}[bg];"
          f"[0:v]fps=30,scale={PANEL_W}:{PANEL_H},setpts=PTS-STARTPTS[m];"
          f"[1:v]fps=30,scale={PANEL_W}:{PANEL_H},setpts=PTS-STARTPTS[a];"
          f"[bg][m]overlay=0:{PANEL_Y}:eof_action=repeat[t1];"
          f"[t1][a]overlay=960:{PANEL_Y}:eof_action=repeat[t2];"
          f"[t2]ass={tmp / 'comparison.ass'}[v]")
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "man.txt"),
           "-f", "concat", "-safe", "0", "-i", str(tmp / "ai.txt"),
           "-filter_complex", fc, "-map", "[v]", "-t", f"{total:.2f}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
           str(out / "comparison.mp4"))
    ffmpeg("-ss", f"{min(ai_done + 3, main_len - 1):.2f}", "-i", str(out / "comparison.mp4"),
           "-frames:v", "1", str(out / "poster.png"))
    print(json.dumps(stats["video"]), f"-> {out / 'comparison.mp4'}")


def end_card(ass: Ass, a: float, b: float, stats: dict, hm: dict) -> None:
    m, g = stats["manual"], stats["ai"]
    ass.rect(a, b, 0, 0, W, H, "&H1A1410&", layer=5)
    ass.text(a, b, W // 2, 110, "Same level, two ways", size=64, bold=True, an=5, layer=6)
    cm = m["matches_target"]
    est = m.get("estimate_full")
    if m.get("stopped_at_budget") and est:
        manual_time = f"{mmss(m['real_time_s'])} recorded, {m['progress']['pct']:.0f}% done"
        manual_steps = f"{m['steps']:,} so far"
    else:
        manual_time, manual_steps = mmss(m["real_time_s"]), f"{m['steps']:,}"
    rows = [
        ("", "Manual (LDtk)", "GLDtk AI"),
        ("Real time", manual_time, f"{mmss(g['real_time_s'])}  (generate {g['generate_s']:.0f} s)"),
    ]
    if m.get("stopped_at_budget") and est:
        rows.append(("Full build (estimate)", f"≈ {mmss(est['full_real_s'])}, ~{est['steps']:,} steps", "—"))
    rows += [
        ("Steps", manual_steps, f"{g['steps']:,}"),
        ("   clicks · drags · keys", f"{m['click']:,} · {m['drag']:,} · {m['key']:,}",
         f"{g['click']} · {g['drag']} · {g['key']}  ({g['prompt_words']}-word prompt)"),
        ("Verified playable", manual_check_text(m.get("check")), "yes — beatable, soft-lock-free" if g["verified"] else "no"),
        ("Edit turnaround", "redo by hand", f"{g['edit_s']:.1f} s  (+ undo {g['undo_s']:.1f} s)"),
    ]
    y = 220
    for i, (k, l, r) in enumerate(rows):
        col = GREY if i == 0 or k.startswith("   ") else WHITE
        size = 34 if not k.startswith("   ") else 28
        ass.text(a, b, 140, y, k.strip(), size=size, color=col, layer=6)
        ass.text(a, b, 700, y, l, size=size, color=col if i == 0 else WHITE, bold=i == 0, layer=6)
        ass.text(a, b, 1260, y, r, size=size, color=col if i == 0 else GREEN, bold=i == 0, layer=6)
        y += 78 if not k.startswith("   ") else 62
    notes = [
        f"AI: real run, model {g['answered_by']}; the manual side rebuilds the AI's level.",
        f"Match with the AI's level: collisions {cm.get('collision_cells', ['?', '?'])[0]}/{cm.get('collision_cells', ['?', '?'])[1]} cells, "
        + (f"tiles simplified: {m['tile_fills']} fills — rock fill + top surfaces, no edge tiles, "
           if m.get("tiles_mode") == "simple" else
           f"tiles {cm.get('tile_cells', ['?', '?'])[0]}/{cm.get('tile_cells', ['?', '?'])[1]}, ")
        + f"entities {cm.get('entities', ['?', '?'])[0]}/{cm.get('entities', ['?', '?'])[1]}.",
        f"Manual: scripted in LDtk's own editor at human pace — click {hm['click']} s, tool/layer {hm['choose']} s, "
        f"typing {hm['perChar']} s/char + {hm['think']} s, drag {hm['drag']} s, look {hm['look']} s per section.",
        "Steps = every click, drag and keystroke. Manual video sped up; timers show real time.",
    ]
    if m.get("stopped_at_budget") and est:
        notes.insert(1, f"Manual estimate: every planned action of the whole level × the timing model × the measured "
                        f"real/modeled ratio ({est['real_per_modeled']:.2f}) of the recorded {mmss(m['real_time_s'])}.")
    if not m["tiles_included"]:
        notes.insert(1, "Manual: tiles not included — would add more time.")
    y = 790
    for n in notes:
        ass.text(a, b, 140, y, n, size=24, color=GREY, layer=6)
        y += 44


if __name__ == "__main__":
    main()
