"""Generate editable Draw.io (.drawio) architecture & DAG diagrams plus SVG/PNG renders.

Outputs under docs/architecture/:
  - 01_pipeline_dag.drawio / .svg
  - 02_system_architecture.drawio / .svg
  - 03_verification_and_mission_dags.drawio / .svg
  - gldtk_architecture_master.drawio (3-page multi-tab workbook)
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
import xml.etree.ElementTree as ET

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "architecture"


@dataclass
class Palette:
    fill: str
    stroke: str
    header_fill: str
    badge_text: str
    text: str = "#0F172A"
    subtext: str = "#334155"


PALETTES: Dict[str, Palette] = {
    "slate": Palette(fill="#F8FAFC", stroke="#64748B", header_fill="#E2E8F0", badge_text="#475569"),
    "indigo": Palette(fill="#EEF2FF", stroke="#4F46E5", header_fill="#C7D2FE", badge_text="#4338CA"),
    "purple": Palette(fill="#F5F3FF", stroke="#7C3AED", header_fill="#DDD6FE", badge_text="#6D28D9"),
    "amber": Palette(fill="#FFFBEB", stroke="#D97706", header_fill="#FDE68A", badge_text="#B45309"),
    "emerald": Palette(fill="#ECFDF5", stroke="#059669", header_fill="#A7F3D0", badge_text="#047857"),
    "teal": Palette(fill="#F0FDFA", stroke="#0D9488", header_fill="#99F6E4", badge_text="#0F766E"),
    "blue": Palette(fill="#EFF6FF", stroke="#2563EB", header_fill="#BFDBFE", badge_text="#1D4ED8"),
    "rose": Palette(fill="#FFF1F2", stroke="#E11D48", header_fill="#FECDD3", badge_text="#BE123C"),
    "dark": Palette(
        fill="#1E293B",
        stroke="#334155",
        header_fill="#0F172A",
        badge_text="#60A5FA",
        text="#F8FAFC",
        subtext="#CBD5E1",
    ),
}


@dataclass
class Group:
    id: str
    title: str
    x: int
    y: int
    w: int
    h: int
    palette: str = "slate"
    subtitle: str = ""


@dataclass
class Node:
    id: str
    title: str
    x: int
    y: int
    w: int
    h: int
    lines: List[str] = field(default_factory=list)
    palette: str = "blue"
    badge: str = ""
    shape: str = "rect"  # "rect" | "pill" | "cylinder"


@dataclass
class Edge:
    id: str
    src: str
    dst: str
    label: str = ""
    exit_pt: Tuple[float, float] = (1.0, 0.5)
    entry_pt: Tuple[float, float] = (0.0, 0.5)
    color: str = "#334155"
    dashed: bool = False
    waypoints: List[Tuple[int, int]] = field(default_factory=list)
    label_pos: Tuple[int, int] | None = None  # Explicit SVG label center; None = midpoint
    label_offset: Tuple[int, int] = (0, 0)
    thick: bool = False
    bidirectional: bool = False


@dataclass
class Diagram:
    id: str
    name: str
    title: str
    subtitle: str
    width: int
    height: int
    groups: List[Group] = field(default_factory=list)
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    legend: List[Tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Draw.io XML builder
# ---------------------------------------------------------------------------

def _mx_escape_html(text: str) -> str:
    return html.escape(text, quote=True)


def _node_label_html(node: Node, pal: Palette) -> str:
    parts: List[str] = []
    if node.badge:
        parts.append(
            f"<font style='font-size:10px' color='{pal.badge_text}'><b>[{_mx_escape_html(node.badge)}]</b></font>"
        )
    parts.append(f"<font style='font-size:12px' color='{pal.text}'><b>{_mx_escape_html(node.title)}</b></font>")
    for line in node.lines:
        parts.append(f"<font style='font-size:10.5px' color='{pal.subtext}'>{_mx_escape_html(line)}</font>")
    return "<br/>".join(parts)


def _group_label_html(group: Group, pal: Palette) -> str:
    if group.subtitle:
        return (
            f"<b>{_mx_escape_html(group.title)}</b>"
            f"<br/><font style='font-size:11px' color='{pal.subtext}'>{_mx_escape_html(group.subtitle)}</font>"
        )
    return f"<b>{_mx_escape_html(group.title)}</b>"


def build_mxgraph_diagram_element(diag: Diagram) -> ET.Element:
    diagram_el = ET.Element("diagram", {"id": diag.id, "name": diag.name})
    model = ET.SubElement(
        diagram_el,
        "mxGraphModel",
        {
            "dx": "1422",
            "dy": "794",
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": str(diag.width),
            "pageHeight": str(diag.height),
            "math": "0",
            "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    title_html = (
        f"<font style='font-size:20px' color='#0F172A'><b>{_mx_escape_html(diag.title)}</b></font>"
        f"<br/><font style='font-size:12px' color='#475569'>{_mx_escape_html(diag.subtitle)}</font>"
    )
    tc = ET.SubElement(
        root,
        "mxCell",
        {
            "id": f"{diag.id}_title",
            "value": title_html,
            "style": "text;html=1;align=left;verticalAlign=top;spacingLeft=4;spacingTop=2;whiteSpace=wrap;rounded=0;",
            "vertex": "1",
            "parent": "1",
        },
    )
    ET.SubElement(tc, "mxGeometry", {"x": "30", "y": "14", "width": str(diag.width - 60), "height": "52", "as": "geometry"})

    for g in diag.groups:
        pal = PALETTES[g.palette]
        style = (
            f"swimlane;whiteSpace=wrap;html=1;rounded=1;arcSize=8;"
            f"startSize={'46' if g.subtitle else '32'};"
            f"fillColor={pal.fill};strokeColor={pal.stroke};strokeWidth=1.8;"
            f"swimlaneFillColor={pal.fill};fontColor={pal.text};fontSize=13;"
        )
        cell = ET.SubElement(
            root,
            "mxCell",
            {"id": g.id, "value": _group_label_html(g, pal), "style": style, "vertex": "1", "parent": "1"},
        )
        ET.SubElement(cell, "mxGeometry", {"x": str(g.x), "y": str(g.y), "width": str(g.w), "height": str(g.h), "as": "geometry"})

    for n in diag.nodes:
        pal = PALETTES[n.palette]
        base = "rounded=1;whiteSpace=wrap;html=1;"
        if n.shape == "pill":
            base += "arcSize=50;"
        elif n.shape == "cylinder":
            base = "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=10;"
        else:
            base += "arcSize=10;"

        style = (
            f"{base}fillColor={pal.fill};strokeColor={pal.stroke};strokeWidth=1.6;"
            f"fontColor={pal.text};fontSize=12;align=center;verticalAlign=middle;spacing=6;"
        )
        cell = ET.SubElement(
            root,
            "mxCell",
            {"id": n.id, "value": _node_label_html(n, pal), "style": style, "vertex": "1", "parent": "1"},
        )
        ET.SubElement(cell, "mxGeometry", {"x": str(n.x), "y": str(n.y), "width": str(n.w), "height": str(n.h), "as": "geometry"})

    for e in diag.edges:
        stroke_w = "2.2" if e.thick else "1.6"
        dash_str = "dashed=1;dashPattern=6 4;" if e.dashed else ""
        start_arrow = "startArrow=classic;startFill=1;" if e.bidirectional else "startArrow=none;"
        style = (
            f"edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;"
            f"exitX={e.exit_pt[0]};exitY={e.exit_pt[1]};exitDx=0;exitDy=0;"
            f"entryX={e.entry_pt[0]};entryY={e.entry_pt[1]};entryDx=0;entryDy=0;"
            f"strokeColor={e.color};strokeWidth={stroke_w};{dash_str}{start_arrow}"
            f"endArrow=classic;endFill=1;fontSize=11;fontColor={e.color};labelBackgroundColor=#FFFFFF;"
        )
        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": e.id,
                "value": _mx_escape_html(e.label),
                "style": style,
                "edge": "1",
                "parent": "1",
                "source": e.src,
                "target": e.dst,
            },
        )
        geom = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        if e.waypoints:
            arr = ET.SubElement(geom, "Array", {"as": "points"})
            for wx, wy in e.waypoints:
                ET.SubElement(arr, "mxPoint", {"x": str(wx), "y": str(wy)})

    return diagram_el


def write_drawio_file(path: Path, diagrams: Sequence[Diagram]) -> None:
    mxfile = ET.Element(
        "mxfile",
        {
            "host": "app.diagrams.net",
            "agent": "GLDtk Architecture Generator",
            "version": "24.7.17",
            "pages": str(len(diagrams)),
        },
    )
    for d in diagrams:
        mxfile.append(build_mxgraph_diagram_element(d))
    ET.indent(mxfile, space="  ")
    path.write_bytes(ET.tostring(mxfile, encoding="utf-8", xml_declaration=True))


# ---------------------------------------------------------------------------
# Companion SVG renderer (exact coordinate parity with Draw.io XML)
# ---------------------------------------------------------------------------

def _anchor_point(node: Node, pt: Tuple[float, float]) -> Tuple[float, float]:
    return (node.x + node.w * pt[0], node.y + node.h * pt[1])


def _orth_path(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    exit_pt: Tuple[float, float],
    entry_pt: Tuple[float, float],
    waypoints: List[Tuple[int, int]],
) -> List[Tuple[float, float]]:
    if waypoints:
        return [p0] + [(float(x), float(y)) for x, y in waypoints] + [p1]

    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    if abs(dx) < 1.5 or abs(dy) < 1.5:
        return [p0, p1]

    exit_horiz = exit_pt[0] in (0.0, 1.0) and exit_pt[1] not in (0.0, 1.0)
    entry_horiz = entry_pt[0] in (0.0, 1.0) and entry_pt[1] not in (0.0, 1.0)

    if exit_horiz and entry_horiz:
        mx = p0[0] + dx * 0.5
        return [p0, (mx, p0[1]), (mx, p1[1]), p1]
    if (not exit_horiz) and (not entry_horiz):
        my = p0[1] + dy * 0.5
        return [p0, (p0[0], my), (p1[0], my), p1]
    if exit_horiz and not entry_horiz:
        return [p0, (p1[0], p0[1]), p1]
    return [p0, (p0[0], p1[1]), p1]


def _svg_rounded_polyline(pts: List[Tuple[float, float]], radius: float = 8.0) -> str:
    if len(pts) < 2:
        return ""
    if len(pts) == 2:
        return f"M {pts[0][0]:.1f},{pts[0][1]:.1f} L {pts[1][0]:.1f},{pts[1][1]:.1f}"

    cmds = [f"M {pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for i in range(1, len(pts) - 1):
        ax, ay = pts[i - 1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        v1x, v1y = bx - ax, by - ay
        v2x, v2y = cx - bx, cy - by
        l1 = math.hypot(v1x, v1y)
        l2 = math.hypot(v2x, v2y)
        if l1 < 1e-3 or l2 < 1e-3:
            cmds.append(f"L {bx:.1f},{by:.1f}")
            continue
        r = min(radius, l1 * 0.45, l2 * 0.45)
        p_in = (bx - v1x / l1 * r, by - v1y / l1 * r)
        p_out = (bx + v2x / l2 * r, by + v2y / l2 * r)
        cmds.append(f"L {p_in[0]:.1f},{p_in[1]:.1f}")
        cmds.append(f"Q {bx:.1f},{by:.1f} {p_out[0]:.1f},{p_out[1]:.1f}")
    cmds.append(f"L {pts[-1][0]:.1f},{pts[-1][1]:.1f}")
    return " ".join(cmds)


def _midpoint_of_polyline(pts: List[Tuple[float, float]]) -> Tuple[float, float]:
    total = 0.0
    segs = []
    for i in range(len(pts) - 1):
        d = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        segs.append((d, pts[i], pts[i + 1]))
        total += d
    if total < 1e-3:
        return pts[0]
    target = total * 0.5
    acc = 0.0
    for d, a, b in segs:
        if acc + d >= target:
            t = (target - acc) / max(d, 1e-6)
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        acc += d
    return pts[-1]


def _fit_font_size(text: str, base_size: float, max_px: float, bold: bool = False) -> float:
    factor = 0.59 if bold else 0.53
    est = len(text) * base_size * factor
    if est <= max_px:
        return base_size
    return max(9.2, base_size * (max_px / est))


def write_svg_file(path: Path, diag: Diagram) -> None:
    node_map = {n.id: n for n in diag.nodes}
    lines: List[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{diag.width}" height="{diag.height}" '
        f'viewBox="0 0 {diag.width} {diag.height}" '
        f'style="background:#FFFFFF;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;">'
    )

    colors = sorted({e.color for e in diag.edges} | {"#334155"})
    lines.append("<defs>")
    lines.append(
        '<filter id="cardShadow" x="-4%" y="-4%" width="108%" height="110%">'
        '<feDropShadow dx="0" dy="2" stdDeviation="2.2" flood-color="#0F172A" flood-opacity="0.08"/>'
        "</filter>"
    )
    for col in colors:
        cid = col.replace("#", "")
        lines.append(
            f'<marker id="arrow_{cid}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="{col}"/>'
            f"</marker>"
        )
    lines.append("</defs>")

    lines.append(f'<rect x="0" y="0" width="{diag.width}" height="{diag.height}" fill="#FFFFFF"/>')

    # Header
    lines.append(
        f'<text x="32" y="38" font-size="22" font-weight="700" fill="#0F172A">{html.escape(diag.title)}</text>'
    )
    lines.append(
        f'<text x="32" y="60" font-size="13" fill="#475569">{html.escape(diag.subtitle)}</text>'
    )

    # Legend pills top-right
    if diag.legend:
        lx = diag.width - 30
        for lbl, pal_name in reversed(diag.legend):
            pal = PALETTES[pal_name]
            tw = max(84, int(len(lbl) * 6.8 + 22))
            lx -= tw + 8
            lines.append(
                f'<rect x="{lx}" y="20" width="{tw}" height="24" rx="12" '
                f'fill="{pal.fill}" stroke="{pal.stroke}" stroke-width="1.4"/>'
            )
            lines.append(
                f'<text x="{lx + tw / 2:.1f}" y="36" font-size="11" font-weight="600" '
                f'text-anchor="middle" fill="{pal.text}">{html.escape(lbl)}</text>'
            )

    # Groups
    for g in diag.groups:
        pal = PALETTES[g.palette]
        hh = 46 if g.subtitle else 32
        lines.append(
            f'<rect x="{g.x}" y="{g.y}" width="{g.w}" height="{g.h}" rx="10" '
            f'fill="{pal.fill}" stroke="{pal.stroke}" stroke-width="1.8"/>'
        )
        lines.append(
            f'<path d="M {g.x},{g.y + hh} L {g.x},{g.y + 10} Q {g.x},{g.y} {g.x + 10},{g.y} '
            f'L {g.x + g.w - 10},{g.y} Q {g.x + g.w},{g.y} {g.x + g.w},{g.y + 10} '
            f'L {g.x + g.w},{g.y + hh} Z" fill="{pal.header_fill}" stroke="{pal.stroke}" stroke-width="1.2"/>'
        )
        gt_size = _fit_font_size(g.title, 13.0, g.w - 26, bold=True)
        lines.append(
            f'<text x="{g.x + 14}" y="{g.y + 21}" font-size="{gt_size:.1f}" font-weight="700" fill="{pal.text}">'
            f"{html.escape(g.title)}</text>"
        )
        if g.subtitle:
            gs_size = _fit_font_size(g.subtitle, 11.0, g.w - 26, bold=False)
            lines.append(
                f'<text x="{g.x + 14}" y="{g.y + 38}" font-size="{gs_size:.1f}" fill="{pal.subtext}">'
                f"{html.escape(g.subtitle)}</text>"
            )

    # Nodes
    for n in diag.nodes:
        pal = PALETTES[n.palette]
        rx = n.h // 2 if n.shape == "pill" else 8
        if n.shape == "cylinder":
            lines.append(
                f'<rect x="{n.x}" y="{n.y + 6}" width="{n.w}" height="{n.h - 6}" rx="8" '
                f'fill="{pal.fill}" stroke="{pal.stroke}" stroke-width="1.6" filter="url(#cardShadow)"/>'
            )
            lines.append(
                f'<ellipse cx="{n.x + n.w/2:.1f}" cy="{n.y + 9}" rx="{n.w/2:.1f}" ry="7" '
                f'fill="{pal.header_fill}" stroke="{pal.stroke}" stroke-width="1.4"/>'
            )
        else:
            lines.append(
                f'<rect x="{n.x}" y="{n.y}" width="{n.w}" height="{n.h}" rx="{rx}" '
                f'fill="{pal.fill}" stroke="{pal.stroke}" stroke-width="1.7" filter="url(#cardShadow)"/>'
            )
            if n.shape != "pill" and n.palette != "dark":
                lines.append(
                    f'<rect x="{n.x}" y="{n.y}" width="6" height="{n.h}" rx="3" fill="{pal.stroke}"/>'
                )

        # Clean micro-pill badge in top-right (or top-center) so title never collides/overflows
        cx = n.x + (n.w + 4) / 2 if n.palette != "dark" and n.shape != "cylinder" else n.x + n.w / 2
        avail_w = n.w - 26
        has_badge = bool(n.badge)
        extra_top = 14.0 if has_badge else 0.0
        cyl_top = 8.0 if n.shape == "cylinder" else 0.0

        total_lines = 1 + len(n.lines)
        line_gap = 15.0
        block_h = total_lines * line_gap + extra_top
        start_y = n.y + cyl_top + (n.h - cyl_top - block_h) / 2 + 11.5

        if has_badge:
            bw = max(46, int(len(n.badge) * 6.4 + 14))
            bx = cx - bw / 2
            by = start_y - 10
            badge_bg = "#334155" if n.palette == "dark" else pal.header_fill
            lines.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw}" height="15" rx="7.5" '
                f'fill="{badge_bg}" stroke="{pal.stroke}" stroke-width="0.9"/>'
            )
            lines.append(
                f'<text x="{cx:.1f}" y="{by + 11:.1f}" text-anchor="middle" font-size="9.5" '
                f'font-weight="700" fill="{pal.badge_text}">{html.escape(n.badge)}</text>'
            )
            start_y += 17.5

        t_size = _fit_font_size(n.title, 12.5, avail_w, bold=True)
        lines.append(
            f'<text x="{cx:.1f}" y="{start_y:.1f}" text-anchor="middle" font-size="{t_size:.1f}" '
            f'font-weight="700" fill="{pal.text}">{html.escape(n.title)}</text>'
        )

        for idx, body_line in enumerate(n.lines, start=1):
            ly = start_y + idx * line_gap
            b_size = _fit_font_size(body_line, 11.0, avail_w, bold=False)
            lines.append(
                f'<text x="{cx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="{b_size:.1f}" '
                f'fill="{pal.subtext}">{html.escape(body_line)}</text>'
            )

    # Edges
    for e in diag.edges:
        s_node = node_map[e.src]
        t_node = node_map[e.dst]
        p0 = _anchor_point(s_node, e.exit_pt)
        p1 = _anchor_point(t_node, e.entry_pt)
        pts = _orth_path(p0, p1, e.exit_pt, e.entry_pt, e.waypoints)
        d = _svg_rounded_polyline(pts, radius=9.0)
        cid = e.color.replace("#", "")
        sw = "2.3" if e.thick else "1.7"
        dash = ' stroke-dasharray="6 4"' if e.dashed else ""
        start_m = f' marker-start="url(#arrow_{cid})"' if e.bidirectional else ""
        lines.append(
            f'<path d="{d}" fill="none" stroke="{e.color}" stroke-width="{sw}"{dash}'
            f'{start_m} marker-end="url(#arrow_{cid})"/>'
        )

        if e.label:
            if e.label_pos is not None:
                lx, ly = float(e.label_pos[0]), float(e.label_pos[1])
            else:
                mx, my = _midpoint_of_polyline(pts)
                lx = mx + e.label_offset[0]
                ly = my + e.label_offset[1]
            lbl_w = max(36, len(e.label) * 6.2 + 12)
            lines.append(
                f'<rect x="{lx - lbl_w/2:.1f}" y="{ly - 9:.1f}" width="{lbl_w:.1f}" height="16" '
                f'rx="4" fill="#FFFFFF" fill-opacity="0.96" stroke="{e.color}" stroke-opacity="0.4" stroke-width="0.9"/>'
            )
            lines.append(
                f'<text x="{lx:.1f}" y="{ly + 2.5:.1f}" text-anchor="middle" font-size="10.2" '
                f'font-weight="600" fill="{e.color}">{html.escape(e.label)}</text>'
            )

    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Diagram 1: End-to-End 11-Stage Neuro-Symbolic Pipeline DAG
# ---------------------------------------------------------------------------

def make_pipeline_dag() -> Diagram:
    groups = [
        Group("g_inputs", "Inputs & Context Assembly", 30, 88, 340, 1020, "indigo",
              "User intent, physics profile, RAG & memory"),
        Group("g_neural", "Stages 1–4 · System 1 Neural Spec & Mission", 425, 88, 520, 1020, "purple",
              "Schema-enforced LLM + symbolic patching & Dormans grammar"),
        Group("g_verify", "Stages 5–8 · System 2 Terrain, Physics & Soft-Lock Proofs", 1005, 88, 530, 1020, "emerald",
              "Swept-hitbox physics oracle, Tarjan SCC & complexity control"),
        Group("g_export", "Stages 9–11 · Noise, Art & LDtk Export", 1595, 88, 355, 1020, "blue",
              "Schema-exact LDtk 1.5.3 JSON + sidebar"),
    ]

    nodes = [
        # Zone 1: Inputs (x=50, w=300)
        Node("in_prompt", "User Prompt & Generation Params", 50, 152, 300, 104, [
            "Natural language description + seed",
            "complexity (0..1), rock fill, levels (1..8)",
            "Target level / section / region scope",
        ], "indigo", "INPUT"),
        Node("in_profile", "GameProfile (game/profile.py)", 50, 286, 300, 118, [
            "File: gldtk.game.json beside .ldtk",
            "Physics: g=1800, jump_v=500, run=220 px/s",
            "Abilities: double_jump, dash, wall_jump",
            "Comfort: hop<=4, rise<=3, drop<=6 tiles",
        ], "indigo", "PHYSICS"),
        Node("in_patterns", "RAG Pattern Library (patterns/)", 50, 434, 300, 118, [
            "index.json: 3,577 indexed records",
            "3,336 verified windows (SuperTux/OpenSurge)",
            "768-dim cosine retrieval (nomic-embed-text)",
            "Vision-checked labels (patterns/vision.py)",
        ], "indigo", "RAG"),
        Node("in_memory", "Design Memory (memory/)", 50, 582, 300, 114, [
            "Local cross-project log (events.jsonl)",
            "Diffs hand edits, undos & accepted builds",
            "preferences.json distilled designer rules",
            "App Support/GLDtk/memory/ (toggleable)",
        ], "indigo", "MEMORY"),
        Node("in_assets", "Curated CC0 Asset Library", 50, 726, 300, 104, [
            "8 autotile themes (80x64 14-role sheets)",
            "25 entity sprites (Kenney CC0 1.0)",
            "Optional Gemini AI art (/art/theme, /sprite)",
        ], "indigo", "ASSETS"),
        Node("in_invariant", "Core Safety Guarantee", 50, 862, 300, 150, [
            "Unverified levels are NEVER written to disk.",
            "Every exported level proves:",
            "1. Swept 12x24 hitbox Start->Exit path",
            "2. 0 SCC traps & 0 item-state soft-locks",
            "3. Validated LDtk 1.5.3 JSON schema",
        ], "dark", "INVARIANT"),

        # Zone 2: Stages 1-4 (x=475, w=420) — leaves corridors x=425..470 & x=895..945
        Node("s1_llm", "1. LLMController & Provider Cascade", 475, 152, 420, 134, [
            "llm/validator.py + llm/providers/registry.py",
            "Primary: gemini-3.8-flash -> 3.1-flash-lite (503 backoff)",
            "Auto Fallback: ollama:qwen2.5:14b (900s local timeout)",
            "Also supports Claude (cached), OpenAI, DeepSeek",
            "Schema-enforced JSON output: LevelSpec.json_schema()",
        ], "purple", "STAGE 1"),
        Node("s2_patch", "2. Spec Validator & SymbolicPatcher", 475, 330, 420, 134, [
            "spec/level_spec.py + llm/patcher.py",
            "SpecNormalizePatch: clamp bounds, scale complexity",
            "EdgeReclassifyPatch: steep Walk -> Jump / Fall",
            "VelocityPatch, AbilityAssignPatch, SteppingStonePatch",
            "Fixes Qwen/LLM drift without extra LLM calls",
        ], "amber", "STAGE 2"),
        Node("s3_comp", "3. Completeness & Fidelity Fill", 475, 512, 420, 134, [
            "pipeline/completeness.py + fidelity/{checker,fill}.py",
            "Extracts explicit & implicit prompt requirements",
            "Deterministically injects missing entities, start_item,",
            "section P_<type> structures, and lock_key_cycle",
            "At most 1 complete_spec() LLM re-prompt if still short",
        ], "amber", "STAGE 3"),
        Node("s4_mission", "4. Mission Grammar & Space Embedding", 475, 694, 420, 136, [
            "mission/{grammar,graph,space,verify}.py",
            "Dormans graph rewriting productions:",
            "lock_key_cycle · ability_gate · shortcut_loop",
            "treasure_branch · boss_before_exit",
            "Counterfactual verify: unbypassable & solvable locks",
        ], "purple", "STAGE 4"),
        Node("s_world", "Multi-Level Worlds & Endless Chunks", 475, 875, 420, 145, [
            "pipeline/world.py & pipeline/chunks.py",
            "WorldOutline -> 1..8 linked levels (Exit -> next Start)",
            "Regenerate single level while keeping sibling levels",
            "POST /chunks appends deterministic seeded chunks",
            "Joined via fixed border port seams & verified per chunk",
        ], "slate", "WORLDS"),

        # Zone 3: Stages 5-8 (x=1065, w=420) — leaves corridors x=1005..1065 & x=1485..1535
        Node("s5_terrain", "5. Terrain Carving & RAG Pattern Stamping", 1065, 152, 420, 140, [
            "layout/terrain/ + pipeline/stamps.py (StampFinder)",
            "7 rock carvers: corridor · parkour · labyrinth ·",
            "arena · shaft (zigzag) · hub · open",
            "Matches RAG windows by geometry class & height;",
            "rock padding & vertical stacking + level_route check",
        ], "teal", "STAGE 5"),
        Node("s6_verify", "6. Grid Extraction, Physics & Soft-Lock Verify", 1065, 344, 420, 156, [
            "symbolic/{extractor,physics,walljump,gravity}.py",
            "layout/verify.py + layout/softlock.py",
            "Swept 12x24 px AABB hitbox (ds <= 6 px), headroom >= 2",
            "Checks single/double jump (tau*), dash, wall kick, g(x)",
            "Tarjan SCC traps + Item-State Product Graph V x P(K)",
            "Counterfactual check: no key placed behind its door",
        ], "emerald", "STAGE 6"),
        Node("s6_repair", "Deterministic Grid & Soft-Lock Repair", 1065, 558, 420, 124, [
            "layout/repair.py + layout/softlock_repair.py",
            "Retry failing terrain section seeds (<= TERRAIN_ATTEMPTS)",
            "Synthesize stepping stones, raise low ceilings, relocate keys",
            "Last resort: controller.repair_spec() (<= 2 LLM retries)",
        ], "rose", "REPAIR"),
        Node("s7_comfort", "7. Comfort Pass & Chebyshev Densification", 1065, 736, 420, 128, [
            "layout/comfort.py + layout/densify.py",
            "Effort-cost A* (walk=1.0, super-linear jump cost)",
            "Adds hop_N ledges for comfort limits (outside parkour)",
            "2D Chebyshev distance transform void platform infill",
            "Discards any fill platform obstructing golden arcs",
        ], "teal", "STAGE 7"),
        Node("s8_complex", "8. Closed-Loop Complexity Controller", 1065, 916, 420, 126, [
            "pipeline/complexity.py + fidelity/difficulty.py",
            "5-factor score: jumps, enemies, hazards, ledges, relief",
            "Iteratively adjusts gaps & density toward target (1..10)",
            "Re-lays out & re-verifies each round (<= MAX_ROUNDS)",
        ], "emerald", "STAGE 8"),

        # Zone 4: Stages 9-11 (x=1620, w=305)
        Node("s9_noise", "9. Noise Cavern Rock Fill", 1620, 165, 305, 132, [
            "layout/fill.py + layout/noise.py",
            "Domain-warped fBm simplex noise",
            "4–5 Cellular Automata smoothing passes",
            "Decorative Background IntGrid layer",
            "Strictly avoids protected route envelope",
        ], "blue", "STAGE 9"),
        Node("s10_aes", "10. Aesthetic Autotiling", 1620, 358, 305, 134, [
            "aesthetic/{library,autotile,themes}.py",
            "Per-section theme Tiles layers",
            "14-role bitmask autotiler + variants",
            "Packs 25 entity icons into sprites.png",
            "Copies assets to <dir>/gldtk_assets/",
        ], "blue", "STAGE 10"),
        Node("s11_export", "11. LDtk 1.5.3 JSON Export", 1620, 554, 305, 142, [
            "layout/ldtk_adapter.py",
            "Strict layer order: Entities -> Tiles -> Collisions",
            "Pre-populates cachedPixelData & __smartColor",
            "Stores gldtk_prompt/sections/spec fields",
            "Validated against LDtk JSON_SCHEMA.json",
        ], "blue", "STAGE 11"),
        Node("s11_ui", "Native LDtk Sidebar Reload", 1620, 760, 305, 158, [
            "ldtk-src/.../ui/GldtkSidebar.hx",
            "ProjectLoader + editor.selectProject() swap",
            "Renders Fidelity Report & Difficulty Curve",
            "Canvas Route Overlay + red soft-lock spans",
            "60 Hz Play Mode modal + 30-step Undo stack",
        ], "dark", "EDITOR"),
    ]

    edges = [
        # Zone 1 -> Zone 2
        Edge("e_in1", "in_prompt", "s1_llm", "prompt + cfg", (1.0, 0.45), (0.0, 0.25), "#4F46E5",
             waypoints=[(398, 199), (398, 185)], label_pos=(398, 172)),
        Edge("e_in2", "in_profile", "s1_llm", "abilities", (1.0, 0.25), (0.0, 0.5), "#4F46E5", dashed=True,
             waypoints=[(386, 315), (386, 219)], label_pos=(398, 266)),
        Edge("e_in3", "in_patterns", "s1_llm", "few-shot RAG", (1.0, 0.25), (0.0, 0.72), "#4F46E5", dashed=True,
             waypoints=[(400, 463), (400, 248)], label_pos=(398, 365)),
        Edge("e_in4", "in_memory", "s1_llm", "rules", (1.0, 0.3), (0.0, 0.9), "#4F46E5", dashed=True,
             waypoints=[(412, 616), (412, 273)], label_pos=(398, 530)),

        # Bottom-track dedicated lanes below groups (y=1075, 1105, 1135) so NO line crosses boxes!
        Edge("e_in_prof_ver", "in_profile", "s6_verify", "physics bounds", (0.5, 1.0), (0.0, 0.8), "#4F46E5", dashed=True,
             waypoints=[(375, 420), (375, 1068), (972, 1068), (972, 469)], label_pos=(670, 1068)),
        Edge("e_in_pat_stamp", "in_patterns", "s5_terrain", "stamp windows", (1.0, 0.7), (0.0, 0.75), "#0D9488", dashed=True,
             waypoints=[(386, 517), (386, 1094), (986, 1094), (986, 257)], label_pos=(670, 1094)),
        Edge("e_in_aes", "in_assets", "s10_aes", "themes + sprites", (1.0, 0.5), (0.0, 0.65), "#2563EB", dashed=True,
             waypoints=[(398, 778), (398, 1120), (1565, 1120), (1565, 445)], label_pos=(980, 1120)),

        # Zone 2 vertical flow + retries in clean left/right gutters
        Edge("e_1_2", "s1_llm", "s2_patch", "LevelSpec JSON", (0.5, 1.0), (0.5, 0.0), "#7C3AED", thick=True,
             label_pos=(685, 307)),
        Edge("e_2_1", "s2_patch", "s1_llm", "re-prompt (<=2)", (1.0, 0.35), (1.0, 0.65), "#D97706", dashed=True,
             waypoints=[(920, 377), (920, 239)], label_pos=(920, 308)),
        Edge("e_2_3", "s2_patch", "s3_comp", "valid spec", (0.5, 1.0), (0.5, 0.0), "#D97706", thick=True,
             label_pos=(685, 488)),
        Edge("e_3_1", "s3_comp", "s1_llm", "<=1 complete_spec", (0.0, 0.35), (0.0, 0.62), "#D97706", dashed=True,
             waypoints=[(448, 559), (448, 235)], label_pos=(448, 412)),
        Edge("e_3_4", "s3_comp", "s4_mission", "complete spec", (0.5, 1.0), (0.5, 0.0), "#7C3AED", thick=True,
             label_pos=(685, 670)),

        # Zone 2 -> Zone 3
        Edge("e_4_5", "s4_mission", "s5_terrain", "sections + mission plan", (1.0, 0.45), (0.0, 0.35), "#0D9488", thick=True,
             waypoints=[(960, 755), (960, 201)], label_pos=(965, 565)),

        # Zone 3 vertical flow & loops
        Edge("e_5_6", "s5_terrain", "s6_verify", "IRLevel grid", (0.5, 1.0), (0.5, 0.0), "#059669", thick=True,
             label_pos=(1275, 317)),
        Edge("e_6_rep", "s6_verify", "s6_repair", "errors / traps", (0.32, 1.0), (0.32, 0.0), "#E11D48", dashed=True,
             label_pos=(1199, 529)),
        Edge("e_rep_6", "s6_repair", "s6_verify", "re-verify", (0.68, 0.0), (0.68, 1.0), "#059669", dashed=True,
             label_pos=(1351, 529)),
        Edge("e_rep_llm", "s6_repair", "s1_llm", "repair_spec() (<=2)", (0.0, 0.5), (1.0, 0.85), "#E11D48", dashed=True,
             waypoints=[(996, 620), (996, 266)], label_pos=(980, 385)),

        Edge("e_6_7", "s6_verify", "s7_comfort", "verified ok", (1.0, 0.65), (1.0, 0.35), "#059669", thick=True,
             waypoints=[(1510, 445), (1510, 780)], label_pos=(1510, 560)),
        Edge("e_7_8", "s7_comfort", "s8_complex", "comfort + dense grid", (0.5, 1.0), (0.5, 0.0), "#059669", thick=True,
             label_pos=(1275, 890)),
        Edge("e_8_5", "s8_complex", "s5_terrain", "re-tune (<=MAX_ROUNDS)", (0.0, 0.5), (0.0, 0.55), "#0D9488", dashed=True,
             waypoints=[(1032, 979), (1032, 229)], label_pos=(1032, 815)),

        # Zone 3 -> Zone 4
        Edge("e_8_9", "s8_complex", "s9_noise", "converged level", (1.0, 0.5), (0.0, 0.5), "#2563EB", thick=True,
             waypoints=[(1558, 979), (1558, 231)], label_pos=(1558, 700)),
        Edge("e_9_10", "s9_noise", "s10_aes", "grid + background", (0.5, 1.0), (0.5, 0.0), "#2563EB", thick=True,
             label_pos=(1772, 327)),
        Edge("e_10_11", "s10_aes", "s11_export", "autotiled layers", (0.5, 1.0), (0.5, 0.0), "#2563EB", thick=True,
             label_pos=(1772, 522)),
        Edge("e_11_ui", "s11_export", "s11_ui", ".ldtk written -> reload", (0.5, 1.0), (0.5, 0.0), "#0F172A", thick=True,
             label_pos=(1772, 728)),
        Edge("e_ui_mem", "s11_ui", "in_memory", "hand-edit / undo events", (0.5, 1.0), (0.5, 1.0), "#64748B", dashed=True,
             waypoints=[(1772, 1146), (200, 1146), (200, 696)], label_pos=(980, 1146)),
    ]

    legend = [
        ("Inputs / Context", "indigo"),
        ("Neural / System 1", "purple"),
        ("Symbolic Patch/Fill", "amber"),
        ("System 2 Verify", "emerald"),
        ("Repair / Errors", "rose"),
        ("LDtk Export", "blue"),
    ]

    return Diagram(
        id="pipeline_dag",
        name="1 - Pipeline DAG",
        title="GLDtk: End-to-End 11-Stage Neuro-Symbolic Level Generation DAG",
        subtitle="Deterministic verification, repair, comfort, complexity, and LDtk 1.5.3 export (pipeline/level.py::build_level)",
        width=1980,
        height=1185,
        groups=groups,
        nodes=nodes,
        edges=edges,
        legend=legend,
    )


# ---------------------------------------------------------------------------
# Diagram 2: Client-Server & Subsystem Component Architecture
# ---------------------------------------------------------------------------

def make_system_architecture() -> Diagram:
    groups = [
        Group("g_fe", "LDtk 1.5.3 Native Desktop App (Electron + Haxe 4.3)", 30, 88, 415, 990, "indigo",
              "Compiled Haxe/JS renderer (ldtk-src/) + Electron process"),
        Group("g_api", "HTTP/JSON Bridge (127.0.0.1:8765)", 510, 88, 290, 990, "amber",
              "js.html.XMLHttpRequest (CSP-allowed)"),
        Group("g_be", "GLDtk Python Backend Engine (server.py · FastAPI / Uvicorn)", 865, 88, 635, 990, "emerald",
              "Packaged via PyInstaller (gldtk-server) or run in .venv"),
        Group("g_ext", "External Models & Persistent Storage", 1565, 88, 355, 990, "slate",
              "Cloud LLM APIs, Local Ollama, Project & App Data"),
    ]

    nodes = [
        # Frontend (x=50, w=375)
        Node("fe_sidebar", "GldtkSidebar.hx (Native Haxe Panel)", 50, 152, 375, 142, [
            "Prompt box, provider/model picker, sliders",
            "Fidelity Report tree (satisfied / approx / missing)",
            "Section dropdown + 'Make this section a...' (no LLM)",
            "Difficulty Curve SVG (hover flashes section)",
            "Design Memory viewer, Settings & API key form",
        ], "indigo", "UI"),
        Node("fe_lasso", "GldtkAreaTool.hx & Edit Modes", 50, 326, 375, 120, [
            "Canvas rectangle lasso tool (h2d.Graphics)",
            "4 Co-creative edit modes: Region · Section ·",
            "Entities · Describe & Locate highlight preview",
            "Undo stack button (.gldtk_history snapshots)",
        ], "indigo", "EDIT TOOL"),
        Node("fe_route", "Live Canvas Route Overlay", 50, 478, 375, 114, [
            "Draws verified Start->Exit path on LDtk canvas",
            "Color-coded walks, hops, drops & abilities used",
            "Highlights soft-lock trap spans in red",
            "Auto-refreshes via POST /route after edits",
        ], "indigo", "OVERLAY"),
        Node("fe_play", "In-Editor 60 Hz Play Mode Modal", 50, 624, 375, 120, [
            "HTML5 Canvas platformer (built into LDtk)",
            "Runs active GameProfile physics (~1 px accuracy)",
            "Simulates keys, doors, checkpoints, pickups, mobs",
            "Draws verified route ghost & soft-lock warnings",
        ], "indigo", "PLAY MODE"),
        Node("fe_loader", "ProjectLoader & Electron Lifecycle", 50, 776, 375, 124, [
            "In-place swap: editor.selectProject() + flush",
            "Ignores unchanged image watcher (no false '*')",
            "Read-only guard on LDtk bundled sample levels",
            "Electron spawns & terminates gldtk-server",
        ], "indigo", "LIFECYCLE"),
        Node("fe_build", "Desktop Distribution", 50, 932, 375, 114, [
            "macOS Apple Silicon: GLDtk.app (322 MB) / .dmg",
            "Windows NSIS build & smoke tests (feat/windows-build)",
            "scripts/build_mac_app.sh + release.yml CI",
        ], "dark", "PACKAGING"),

        # HTTP Bridge (x=530, w=250)
        Node("api_gen", "Generation & Async Jobs", 530, 158, 250, 126, [
            "POST /generate (level / world)",
            "POST /chunks (endless chunks)",
            "GET /progress (live job steps)",
            "POST /cancel (aborts retries)",
        ], "amber", "HTTP"),
        Node("api_edit", "Co-Creative Editing & Undo", 530, 324, 250, 146, [
            "POST /edit/region",
            "POST /edit/section",
            "POST /edit/entities",
            "POST /edit/locate",
            "POST /undo  ·  GET /history",
        ], "amber", "HTTP"),
        Node("api_analysis", "Verification & Analytics", 530, 508, 250, 120, [
            "GET /route  ·  POST /route",
            "GET /difficulty",
            "GET /profile  ·  PUT /profile",
            "GET /themes  ·  GET /providers",
        ], "amber", "HTTP"),
        Node("api_mem", "Memory, Settings & Art", 530, 666, 250, 140, [
            "GET|PUT|DELETE /memory",
            "POST /memory/scan",
            "GET|POST /settings",
            "POST /art/theme · /art/sprite",
            "GET /art/library · GET /health",
        ], "amber", "HTTP"),
        Node("api_models", "API Contracts (api/models.py)", 530, 846, 250, 118, [
            "GenerateRequest, EditRequest",
            "RouteModel, DifficultyModel",
            "api/jobs.py async job manager",
            "api/samples.py write guard",
        ], "slate", "CONTRACTS"),

        # Backend Modules (x=890 and x=1200, w=275; center corridor x=1165..1200)
        Node("be_llm", "llm/ · Neural Orchestration", 890, 152, 275, 134, [
            "LLMController, prompt.py (cached)",
            "validator.py + SymbolicPatcher",
            "providers/: gemini, anthropic,",
            "ollama, openai_compat, fallback",
        ], "purple", "LLM"),
        Node("be_spec", "spec/ & fidelity/", 1205, 152, 275, 134, [
            "LevelSpec, SpecNode, SpecEdge",
            "9-kind typed EntityCatalog",
            "checker.py + fill.py (auto items)",
            "difficulty.py (5-factor model)",
        ], "amber", "SPEC"),
        Node("be_pipe", "pipeline/ · Orchestrator", 890, 326, 275, 134, [
            "level.py (build_level 11 stages)",
            "completeness.py + complexity.py",
            "world.py (multi-level worlds)",
            "chunks.py + stamps.py",
        ], "emerald", "PIPELINE"),
        Node("be_mission", "mission/ · Dormans Grammars", 1205, 326, 275, 134, [
            "grammar.py: 5 rewrite rules",
            "graph.py + space.py embedding",
            "verify.py counterfactual solver",
            "Locks, keys, gates & boss arenas",
        ], "purple", "MISSION"),
        Node("be_terrain", "layout/ & layout/terrain/", 890, 498, 275, 148, [
            "7 rock carvers: corridor, parkour,",
            "labyrinth, arena, shaft, hub, open",
            "stamp.py (rock pad + stack)",
            "comfort.py, densify.py, fill.py,",
            "noise.py, ldtk_adapter/reader.py",
        ], "teal", "LAYOUT"),
        Node("be_sym", "symbolic/ & game/", 1205, 498, 275, 148, [
            "physics.py (single/double/dash)",
            "walljump.py + gravity.py",
            "extractor.py (grid -> digraph)",
            "softlock.py (Tarjan SCC + V x P(K))",
            "GameProfile (gldtk.game.json)",
        ], "emerald", "ORACLE"),
        Node("be_edit", "edit/ · Regional Splicing", 890, 684, 275, 134, [
            "context.py (lock crossing seams)",
            "region.py / entities.py / locate.py",
            "stitch.py (1-tile margin autotile)",
            "history.py (<=30 snapshots)",
        ], "teal", "EDIT"),
        Node("be_rag", "patterns/ · RAG Index", 1205, 684, 275, 134, [
            "store.py (index.json + vectors)",
            "ingest.py + gaps.py (+641 wins)",
            "classify.py (cycle rank, dead ends)",
            "vision.py (rendered PNG audit)",
        ], "blue", "RAG"),
        Node("be_mem", "memory/ · Design Memory", 890, 856, 275, 128, [
            "handedit.py (snapshot vs edit diff)",
            "approve.py + hooks.py",
            "analyze.py -> preference rules",
            "store.py (events.jsonl + prefs)",
        ], "blue", "MEMORY"),
        Node("be_aes", "aesthetic/ & art/", 1205, 856, 275, 128, [
            "library.py (assets/manifest.json)",
            "autotile.py (14-role bitmasks)",
            "themes.py (8 CC0 themes)",
            "art/ (AI image + quantization)",
        ], "blue", "AESTHETIC"),

        # External Models & Storage (x=1585, w=315)
        Node("ext_cloud", "Cloud LLM & Vision Providers", 1585, 152, 315, 134, [
            "Google Gemini: 3.8-flash, 3.1-flash-lite,",
            "3.5-flash-lite (vision), 3.1-flash-image",
            "Anthropic Claude: sonnet-5-5 (cached)",
            "OpenAI gpt-4o-mini · DeepSeek chat",
        ], "purple", "CLOUD AI"),
        Node("ext_ollama", "Local Ollama Runtime (:11434)", 1585, 326, 315, 132, [
            "qwen2.5:14b (automatic fallback)",
            "llama3.1 (alternative local LLM)",
            "qwen2.5vl:7b (local pattern vision)",
            "nomic-embed-text (768-dim vectors)",
        ], "purple", "LOCAL AI"),
        Node("ext_proj", "Project Files on Disk", 1585, 498, 315, 166, [
            "<project>.ldtk (LDtk 1.5.3 JSON)",
            "gldtk.game.json (GameProfile)",
            "gldtk_assets/themes/*.png",
            "gldtk_assets/sprites.png",
            ".gldtk_history/<stem>/ (undo snapshots)",
        ], "slate", "PROJECT DISK", shape="cylinder"),
        Node("ext_appdata", "User App Support Directory", 1585, 704, 315, 166, [
            "~/Library/Application Support/GLDtk/",
            "(%APPDATA%\\GLDtk on Windows)",
            ".env (per-provider API keys)",
            "patterns/index.json (3,577 records)",
            "memory/{events.jsonl, preferences.json}",
        ], "slate", "APP DATA", shape="cylinder"),
        Node("ext_eval", "Offline Eval & Test Suite", 1585, 908, 315, 132, [
            "tests/: 737 offline tests (FakeProvider)",
            "eval/: 35-prompt benchmark + replay traces",
            "demo/: Playwright CDP side-by-side harness",
        ], "dark", "QA & EVAL"),
    ]

    edges = [
        # Frontend -> HTTP Bridge (gutter x=450..505)
        Edge("e_fe1", "fe_sidebar", "api_gen", "POST /generate", (1.0, 0.35), (0.0, 0.35), "#2563EB", thick=True,
             waypoints=[(477, 202), (477, 202)], label_pos=(477, 184)),
        Edge("e_fe2", "fe_lasso", "api_edit", "POST /edit/*", (1.0, 0.5), (0.0, 0.5), "#2563EB", thick=True,
             label_pos=(477, 374)),
        Edge("e_fe3", "fe_route", "api_analysis", "/route", (1.0, 0.5), (0.0, 0.45), "#0D9488",
             waypoints=[(477, 535), (477, 562)], label_pos=(477, 518)),
        Edge("e_fe4", "fe_sidebar", "api_mem", "/memory & cfg", (1.0, 0.8), (0.0, 0.45), "#64748B", dashed=True,
             waypoints=[(468, 266), (468, 729)], label_pos=(468, 615)),

        # HTTP Bridge -> Backend (gutter x=805..865)
        Edge("e_api_pipe", "api_gen", "be_pipe", "build_level", (1.0, 0.5), (0.0, 0.35), "#059669", thick=True,
             waypoints=[(832, 221), (832, 373)], label_pos=(832, 268)),
        Edge("e_api_edit", "api_edit", "be_edit", "seam edit", (1.0, 0.5), (0.0, 0.45), "#0D9488", thick=True,
             waypoints=[(822, 397), (822, 744)], label_pos=(822, 575)),
        Edge("e_api_ana", "api_analysis", "be_sym", "route & difficulty", (1.0, 0.5), (0.0, 0.35), "#059669",
             waypoints=[(842, 568), (842, 662), (1186, 662), (1186, 550)], label_pos=(1020, 662)),
        Edge("e_api_mem", "api_mem", "be_mem", "prefs & scan", (1.0, 0.35), (0.0, 0.5), "#2563EB",
             waypoints=[(836, 715), (836, 920)], label_pos=(836, 825)),

        # Internal Backend
        Edge("e_be_lp", "be_pipe", "be_llm", "generate_spec", (0.5, 0.0), (0.5, 1.0), "#7C3AED", bidirectional=True,
             label_pos=(1027, 306)),
        Edge("e_be_ps", "be_llm", "be_spec", "validate", (1.0, 0.5), (0.0, 0.5), "#D97706",
             label_pos=(1185, 202)),
        Edge("e_be_pm", "be_pipe", "be_mission", "rewrite", (1.0, 0.5), (0.0, 0.5), "#7C3AED",
             label_pos=(1185, 376)),
        Edge("e_be_pt", "be_pipe", "be_terrain", "carve/stamp", (0.5, 1.0), (0.5, 0.0), "#0D9488",
             label_pos=(1027, 479)),
        Edge("e_be_ts", "be_terrain", "be_sym", "verify & repair", (1.0, 0.5), (0.0, 0.5), "#059669", bidirectional=True,
             label_pos=(1185, 554)),
        Edge("e_be_es", "be_edit", "be_sym", "re-verify", (1.0, 0.3), (0.0, 0.8), "#059669",
             waypoints=[(1186, 724), (1186, 616)], label_pos=(1186, 695)),
        Edge("e_be_tr", "be_terrain", "be_rag", "StampFinder", (1.0, 0.85), (0.0, 0.25), "#2563EB", dashed=True,
             waypoints=[(1186, 624), (1186, 718)], label_pos=(1250, 666)),

        # Backend -> External (gutter x=1500..1565)
        Edge("e_ext_cloud", "be_llm", "ext_cloud", "HTTPS API", (1.0, 0.25), (0.0, 0.45), "#7C3AED", thick=True,
             waypoints=[(1185, 185), (1185, 128), (1532, 128), (1532, 212)], label_pos=(1365, 128)),
        Edge("e_ext_ol", "be_llm", "ext_ollama", "local fallback", (1.0, 0.7), (0.0, 0.45), "#7C3AED", dashed=True,
             waypoints=[(1178, 246), (1178, 306), (1532, 306), (1532, 385)], label_pos=(1365, 306)),
        Edge("e_ext_proj", "be_terrain", "ext_proj", "export .ldtk", (1.0, 0.7), (0.0, 0.4), "#059669", thick=True,
             waypoints=[(1178, 602), (1178, 662), (1532, 662), (1532, 564)], label_pos=(1532, 622)),
        Edge("e_ext_app", "be_rag", "ext_appdata", "index.json", (1.0, 0.5), (0.0, 0.45), "#2563EB", dashed=True,
             waypoints=[(1532, 751), (1532, 779)], label_pos=(1532, 764)),
        Edge("e_ext_mem", "be_mem", "ext_appdata", "events.jsonl", (1.0, 0.6), (0.0, 0.8), "#2563EB", dashed=True,
             waypoints=[(1185, 933), (1185, 1005), (1532, 1005), (1532, 837)], label_pos=(1365, 1005)),

        # Bottom routing track below all boxes (y=1100) for ProjectLoader -> ext_proj
        Edge("e_fe_load", "fe_loader", "ext_proj", "reads .ldtk & sprites", (1.0, 0.8), (0.0, 0.78), "#334155", dashed=True,
             waypoints=[(450, 875), (450, 1102), (1548, 1102), (1548, 628)], label_pos=(980, 1102)),
    ]

    legend = [
        ("LDtk Haxe UI", "indigo"),
        ("HTTP Bridge", "amber"),
        ("Neural / Mission", "purple"),
        ("Verify / Pipeline", "emerald"),
        ("Terrain / Edit", "teal"),
        ("Storage / RAG", "blue"),
    ]

    return Diagram(
        id="system_arch",
        name="2 - System Architecture",
        title="GLDtk: Client-Server & Subsystem Component Architecture",
        subtitle="Native LDtk 1.5.3 Electron/Haxe sidebar communicating with FastAPI neuro-symbolic backend on 127.0.0.1:8765",
        width=1950,
        height=1145,
        groups=groups,
        nodes=nodes,
        edges=edges,
        legend=legend,
    )


# ---------------------------------------------------------------------------
# Diagram 3: Verification, Soft-Lock SCC/Automaton & Mission/Edit Sub-DAGs
# ---------------------------------------------------------------------------

def make_verification_dags() -> Diagram:
    groups = [
        Group("g_oracle", "Sub-DAG A · Kinematic Physics Oracle & Swept-Hitbox Verification (symbolic/)", 30, 88, 910, 535, "emerald",
              "Evaluates continuous Newtonian arcs & 12x24 px AABB clearance across heterogeneous gravity bands"),
        Group("g_softlock", "Sub-DAG B · Soft-Lock Freedom Proofs & Auto-Repair (layout/softlock*.py)", 970, 88, 910, 535, "rose",
              "Guarantees every reachable platform and inventory state can still reach an Exit"),
        Group("g_mission", "Sub-DAG C1 · Dormans Mission Grammar (mission/)", 30, 655, 910, 530, "purple",
              "Separates abstract lock-key progression from spatial room carving"),
        Group("g_edit", "Sub-DAG C2 · Seam-Locked Regional Editing (edit/)", 970, 655, 910, 530, "teal",
              "Non-destructive co-creation preserving manual edits & border connectivity"),
    ]

    nodes = [
        # Sub-DAG A (x=55..915)
        Node("oa_ext", "1. Grid -> Graph Extractor", 55, 165, 235, 134, [
            "symbolic/extractor.py",
            "Scan IntGrid surface runs",
            "Split spans lacking >=2 tiles",
            "overhead headroom (12x24 px)",
            "Detect vertical WallFaces",
        ], "teal", "EXTRACT"),
        Node("oa_single", "Single Parabolic Jump", 335, 152, 275, 76, [
            "0.5*g*t^2 - v_y*t + dy = 0",
            "D = v_y^2 - 2g*dy >= 0; |dx/t*| <= v_run",
        ], "emerald"),
        Node("oa_double", "O(1) Double-Jump Apex", 335, 240, 275, 76, [
            "Closed-form optimal launch time:",
            "tau* = min((v1+sqrt((v1^2+v2^2-2g*dy)/2))/g, 2v1/g)",
        ], "emerald"),
        Node("oa_dash", "Air Dash & Wall-Jump Climb", 335, 328, 275, 76, [
            "Dash: |dx|' = max(0, |dx|-d_dash), g_eff=0",
            "Wall: <=6 kicks, <=400 arcs, WallFace check",
        ], "emerald"),
        Node("oa_grav", "Piecewise Cross-Section Gravity", 335, 416, 275, 76, [
            "symbolic/gravity.py (Flight / path_moment)",
            "G = 2 * int_0^1 (1-s) * g(x(s)) ds",
        ], "emerald"),
        Node("oa_sweep", "2. Swept 12x24 px AABB Check", 655, 175, 260, 128, [
            "trajectory_clear() at ds <= 6 px",
            "Rejects solid ceiling/wall clip",
            "Rejects hazard spike intersection",
            "Tags edge with best_ability()",
        ], "blue", "SWEEP"),
        Node("oa_astar", "3. Effort-Cost A* Route", 655, 348, 260, 134, [
            "symbolic/routes / layout/route.py",
            "Walk cost = 1.0/tile (walk-first)",
            "Super-linear penalty on jumps",
            "Feeds Route Overlay & Comfort Pass",
        ], "blue", "ROUTE"),
        Node("oa_note", "Double-jump tau* matched 20,000 brute-force searches with 0 mismatches (tests/test_abilities.py)",
             55, 524, 860, 74, ["Plain LDtk levels without gldtk_sections allow 1-tile step-ups everywhere (PR #4)."], "slate", "VERIFIED"),

        # Sub-DAG B (x=995..1855) — starts at y>=174 so re-extract loop at y=152 never hits header!
        Node("sb_in", "1. Extracted Digraph G = (V, E)", 995, 184, 225, 126, [
            "Standable platform spans V",
            "Verified Walk/Jump/Fall edges",
            "Keys, abilities & locked doors",
        ], "slate", "INPUT"),
        Node("sb_scc", "2A. Tarjan SCC Condensation", 1265, 174, 285, 124, [
            "Condense G into DAG D = (C, E_cond)",
            "Mark Exit SCCs Live; reverse BFS",
            "V_trap = Reach(Start) \\ V_live",
        ], "rose", "SCC"),
        Node("sb_prod", "2B. Item Automaton S = V x P(K)", 1265, 322, 285, 146, [
            "Tracks up to |K|<=6 items (<=64|V|)",
            "Forward BFS from (Start, {}) via doors",
            "Reverse BFS on S_rev from (V_exit, *)",
            "Counterfactual: k_d in Reach(G\\{d}, Start)",
        ], "rose", "PRODUCT"),
        Node("sb_rep", "3. Deterministic Soft-Lock Repair", 1605, 174, 250, 142, [
            "layout/softlock_repair.py",
            "Classifies traps: 'pit' vs 'sealed'",
            "Inserts <=4 escape stepping stones",
            "Relocates keys trapped past drops",
        ], "amber", "REPAIR"),
        Node("sb_ok", "4. SoftlockReport.ok == True", 1605, 356, 250, 112, [
            "0 movement traps, 0 deadlocks",
            "0 key-behind-door inversions",
            "Red spans shown in Play Mode",
        ], "dark", "PROOF"),
        Node("sb_note", "Every reachable state (u, I) retains a physically verified path to an Exit span",
             995, 524, 860, 74, ["Eliminates classic one-way pit drops and irreversible key expenditure traps."], "slate", "INVARIANT"),

        # Sub-DAG C1: Mission Grammar (x=55..915)
        Node("mc_req", "Prompt & Spec Requirements", 55, 725, 240, 120, [
            "Keys, locked doors, abilities,",
            "boss arenas, shortcut loops",
            "Completeness adds lock_key_cycle",
        ], "purple", "INPUT"),
        Node("mc_gram", "Dormans Rewrite Productions", 345, 720, 270, 138, [
            "mission/grammar.py",
            "lock_key_cycle · ability_gate",
            "shortcut_loop · treasure_branch",
            "boss_before_exit",
        ], "purple", "GRAMMAR"),
        Node("mc_space", "Space Embedding into Carvers", 665, 720, 250, 138, [
            "mission/space.py",
            "Assigns mission roles to",
            "carved terrain sections &",
            "semantic room/dead-end anchors",
        ], "teal", "SPACE"),
        Node("mc_ver", "Counterfactual Mission Verification", 345, 915, 570, 144, [
            "mission/verify.py — checks both necessity and sufficiency:",
            "1. Without key / ability: Exit is NOT reachable (locks are unbypassable)",
            "2. With key / ability: Exit IS reachable with 0 soft-locks",
            "3. Structural fidelity check passes ('key in cave, door in castle')",
        ], "emerald", "COUNTERFACTUAL"),

        # Sub-DAG C2: Regional Editing (x=995..1855)
        Node("ed_sel", "1. Designer Selection & Mode", 995, 720, 245, 136, [
            "Region (GldtkAreaTool lasso)",
            "Section (dropdown / structure)",
            "Entities (selected / rect / level)",
            "Describe (POST /edit/locate)",
        ], "teal", "SELECT"),
        Node("ed_seam", "2. Context & Seam Locking", 1295, 720, 265, 136, [
            "edit/context.py",
            "Read current .ldtk -> extract graph",
            "Lock crossing walk/jump/fall cells",
            "on region border (<=3 cells/seam)",
        ], "amber", "SEAM LOCK"),
        Node("ed_gen", "3. Local Spec & Splice", 1610, 720, 245, 136, [
            "RegionSpec in local coords",
            "(structure edit skips LLM)",
            "edit/stitch.py splices JSON",
            "Re-autotile 1-tile margin",
        ], "teal", "SPLICE"),
        Node("ed_ver", "4. Whole-Level Re-Verification & Snapshot Undo", 1295, 915, 560, 144, [
            "edit/check.py::verify_level() on full stitched level (SEAM_BROKEN / SEAM_DEAD_END)",
            "Deterministic local repair -> <=1 LLM re-prompt -> refuse write if still broken",
            "Snapshot prior .ldtk into .gldtk_history/<stem>/ (last 30 kept) before atomic save",
            "Preserves all manual tile & entity edits outside the edited bounds byte-for-byte",
        ], "dark", "NON-DESTRUCTIVE"),
    ]

    edges = [
        # Sub-DAG A
        Edge("ea_1", "oa_ext", "oa_single", "", (1.0, 0.2), (0.0, 0.5), "#059669"),
        Edge("ea_2", "oa_ext", "oa_double", "", (1.0, 0.4), (0.0, 0.5), "#059669"),
        Edge("ea_3", "oa_ext", "oa_dash", "", (1.0, 0.65), (0.0, 0.5), "#059669"),
        Edge("ea_4", "oa_ext", "oa_grav", "", (1.0, 0.85), (0.0, 0.5), "#059669"),
        Edge("ea_5", "oa_single", "oa_sweep", "", (1.0, 0.5), (0.0, 0.2), "#2563EB"),
        Edge("ea_6", "oa_double", "oa_sweep", "", (1.0, 0.5), (0.0, 0.45), "#2563EB"),
        Edge("ea_7", "oa_dash", "oa_sweep", "", (1.0, 0.5), (0.0, 0.7), "#2563EB"),
        Edge("ea_8", "oa_grav", "oa_sweep", "", (1.0, 0.5), (0.0, 0.9), "#2563EB"),
        Edge("ea_9", "oa_sweep", "oa_astar", "clear arcs", (0.5, 1.0), (0.5, 0.0), "#2563EB", thick=True,
             label_pos=(785, 325)),

        # Sub-DAG B
        Edge("eb_1", "sb_in", "sb_scc", "G_sub", (1.0, 0.35), (0.0, 0.5), "#E11D48", label_pos=(1242, 222)),
        Edge("eb_2", "sb_in", "sb_prod", "G + items", (1.0, 0.75), (0.0, 0.5), "#E11D48", label_pos=(1242, 355)),
        Edge("eb_3", "sb_scc", "sb_rep", "V_trap != 0", (1.0, 0.45), (0.0, 0.35), "#E11D48", dashed=True, label_pos=(1578, 214)),
        Edge("eb_4", "sb_prod", "sb_rep", "deadlock / bad key", (1.0, 0.28), (0.0, 0.75), "#E11D48", dashed=True,
             waypoints=[(1576, 363), (1576, 280)], label_pos=(1578, 314)),
        Edge("eb_5", "sb_rep", "sb_in", "re-extract", (0.5, 0.0), (0.5, 0.0), "#059669", dashed=True,
             waypoints=[(1730, 152), (1107, 152)], label_pos=(1415, 152)),
        Edge("eb_6", "sb_scc", "sb_ok", "0 traps", (1.0, 0.8), (0.0, 0.3), "#059669",
             waypoints=[(1566, 273), (1566, 390)], label_pos=(1566, 368)),
        Edge("eb_7", "sb_prod", "sb_ok", "0 deadlocks", (1.0, 0.75), (0.0, 0.65), "#059669", label_pos=(1578, 434)),

        # Sub-DAG C1
        Edge("ec1_1", "mc_req", "mc_gram", "mission rules", (1.0, 0.5), (0.0, 0.5), "#7C3AED", thick=True, label_pos=(320, 774)),
        Edge("ec1_2", "mc_gram", "mc_space", "mission DAG", (1.0, 0.5), (0.0, 0.5), "#7C3AED", thick=True, label_pos=(640, 774)),
        Edge("ec1_3", "mc_space", "mc_ver", "embedded grid", (0.5, 1.0), (0.78, 0.0), "#059669", thick=True, label_pos=(790, 886)),
        Edge("ec1_4", "mc_gram", "mc_ver", "expected locks", (0.5, 1.0), (0.24, 0.0), "#7C3AED", dashed=True, label_pos=(480, 886)),

        # Sub-DAG C2
        Edge("ec2_1", "ed_sel", "ed_seam", "rect / scope", (1.0, 0.5), (0.0, 0.5), "#0D9488", thick=True, label_pos=(1268, 774)),
        Edge("ec2_2", "ed_seam", "ed_gen", "locked borders", (1.0, 0.5), (0.0, 0.5), "#D97706", thick=True, label_pos=(1585, 774)),
        Edge("ec2_3", "ed_gen", "ed_ver", "stitched JSON", (0.5, 1.0), (0.78, 0.0), "#0F172A", thick=True, label_pos=(1732, 886)),
        Edge("ec2_4", "ed_seam", "ed_ver", "baseline & seams", (0.5, 1.0), (0.24, 0.0), "#D97706", dashed=True, label_pos=(1427, 886)),
    ]

    legend = [
        ("Kinematic Oracle", "emerald"),
        ("Soft-Lock Proof", "rose"),
        ("Mission Grammar", "purple"),
        ("Regional Edit", "teal"),
        ("Repair / Seams", "amber"),
        ("Guarantee", "dark"),
    ]

    return Diagram(
        id="verification_dags",
        name="3 - Verification & Mission DAGs",
        title="GLDtk: Formal Verification, Soft-Lock SCC/Automaton, Mission & Seam-Locked Edit DAGs",
        subtitle="Mathematical guarantees & non-destructive co-creative editing subsystems",
        width=1910,
        height=1215,
        groups=groups,
        nodes=nodes,
        edges=edges,
        legend=legend,
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    diagrams = [
        ("01_pipeline_dag", make_pipeline_dag()),
        ("02_system_architecture", make_system_architecture()),
        ("03_verification_and_mission_dags", make_verification_dags()),
    ]
    for stem, diag in diagrams:
        drawio_path = OUT_DIR / f"{stem}.drawio"
        svg_path = OUT_DIR / f"{stem}.svg"
        write_drawio_file(drawio_path, [diag])
        write_svg_file(svg_path, diag)
        print(f"Wrote {drawio_path.relative_to(OUT_DIR.parent.parent)} and {svg_path.relative_to(OUT_DIR.parent.parent)}")

    master_path = OUT_DIR / "gldtk_architecture_master.drawio"
    write_drawio_file(master_path, [d for _, d in diagrams])
    print(f"Wrote {master_path.relative_to(OUT_DIR.parent.parent)} (3 pages)")


if __name__ == "__main__":
    main()
