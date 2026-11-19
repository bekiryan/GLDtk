"""Generate clean, high-level Draw.io (.drawio) architecture & DAG diagrams plus SVG renders.

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
from typing import Dict, List, Optional, Sequence, Tuple
import xml.etree.ElementTree as ET

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "architecture"


@dataclass
class Palette:
    fill: str
    stroke: str
    header_fill: str
    text: str = "#0F172A"
    subtext: str = "#475569"


PALETTES: Dict[str, Palette] = {
    "slate": Palette(fill="#F8FAFC", stroke="#64748B", header_fill="#E2E8F0"),
    "indigo": Palette(fill="#EEF2FF", stroke="#4F46E5", header_fill="#C7D2FE"),
    "purple": Palette(fill="#F5F3FF", stroke="#7C3AED", header_fill="#DDD6FE"),
    "amber": Palette(fill="#FFFBEB", stroke="#D97706", header_fill="#FDE68A"),
    "emerald": Palette(fill="#ECFDF5", stroke="#059669", header_fill="#A7F3D0"),
    "teal": Palette(fill="#F0FDFA", stroke="#0D9488", header_fill="#99F6E4"),
    "blue": Palette(fill="#EFF6FF", stroke="#2563EB", header_fill="#BFDBFE"),
    "rose": Palette(fill="#FFF1F2", stroke="#E11D48", header_fill="#FECDD3"),
    "dark": Palette(fill="#1E293B", stroke="#334155", header_fill="#0F172A", text="#F8FAFC", subtext="#CBD5E1"),
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


@dataclass
class Node:
    id: str
    title: str
    subtitle: str
    x: int
    y: int
    w: int
    h: int
    palette: str = "blue"
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
    label_pos: Optional[Tuple[int, int]] = None
    thick: bool = True
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

def _mx_escape(text: str) -> str:
    return html.escape(text, quote=True)


def _node_label_html(node: Node, pal: Palette) -> str:
    t = f"<font style='font-size:15px' color='{pal.text}'><b>{_mx_escape(node.title)}</b></font>"
    if not node.subtitle:
        return t
    s = f"<font style='font-size:12px' color='{pal.subtext}'>{_mx_escape(node.subtitle)}</font>"
    return f"{t}<br/>{s}"


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
        f"<font style='font-size:22px' color='#0F172A'><b>{_mx_escape(diag.title)}</b></font>"
        f"<br/><font style='font-size:13px' color='#475569'>{_mx_escape(diag.subtitle)}</font>"
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
    ET.SubElement(tc, "mxGeometry", {"x": "32", "y": "18", "width": str(diag.width - 64), "height": "54", "as": "geometry"})

    for g in diag.groups:
        pal = PALETTES[g.palette]
        style = (
            f"swimlane;whiteSpace=wrap;html=1;rounded=1;arcSize=8;startSize=36;"
            f"fillColor={pal.fill};strokeColor={pal.stroke};strokeWidth=1.8;"
            f"swimlaneFillColor={pal.fill};fontColor={pal.text};fontSize=14;"
        )
        cell = ET.SubElement(
            root,
            "mxCell",
            {"id": g.id, "value": f"<b>{_mx_escape(g.title)}</b>", "style": style, "vertex": "1", "parent": "1"},
        )
        ET.SubElement(cell, "mxGeometry", {"x": str(g.x), "y": str(g.y), "width": str(g.w), "height": str(g.h), "as": "geometry"})

    for n in diag.nodes:
        pal = PALETTES[n.palette]
        if n.shape == "pill":
            base = "rounded=1;arcSize=50;whiteSpace=wrap;html=1;"
        elif n.shape == "cylinder":
            base = "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=10;"
        else:
            base = "rounded=1;arcSize=12;whiteSpace=wrap;html=1;"

        style = (
            f"{base}fillColor={pal.fill};strokeColor={pal.stroke};strokeWidth=1.8;"
            f"fontColor={pal.text};align=center;verticalAlign=middle;spacing=10;"
        )
        cell = ET.SubElement(
            root,
            "mxCell",
            {"id": n.id, "value": _node_label_html(n, pal), "style": style, "vertex": "1", "parent": "1"},
        )
        ET.SubElement(cell, "mxGeometry", {"x": str(n.x), "y": str(n.y), "width": str(n.w), "height": str(n.h), "as": "geometry"})

    for e in diag.edges:
        sw = "2.4" if e.thick else "1.8"
        dash_str = "dashed=1;dashPattern=6 4;" if e.dashed else ""
        start_arrow = "startArrow=classic;startFill=1;" if e.bidirectional else "startArrow=none;"
        style = (
            f"edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;"
            f"exitX={e.exit_pt[0]};exitY={e.exit_pt[1]};exitDx=0;exitDy=0;"
            f"entryX={e.entry_pt[0]};entryY={e.entry_pt[1]};entryDx=0;entryDy=0;"
            f"strokeColor={e.color};strokeWidth={sw};{dash_str}{start_arrow}"
            f"endArrow=classic;endFill=1;fontSize=11.5;fontColor={e.color};labelBackgroundColor=#FFFFFF;"
        )
        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": e.id,
                "value": _mx_escape(e.label),
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
# Clean Companion SVG Renderer
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


def _svg_rounded_polyline(pts: List[Tuple[float, float]], radius: float = 10.0) -> str:
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
    factor = 0.60 if bold else 0.54
    est = len(text) * base_size * factor
    if est <= max_px:
        return base_size
    return max(10.0, base_size * (max_px / est))


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
        '<filter id="cardShadow" x="-4%" y="-4%" width="108%" height="112%">'
        '<feDropShadow dx="0" dy="2.5" stdDeviation="3" flood-color="#0F172A" flood-opacity="0.08"/>'
        "</filter>"
    )
    for col in colors:
        cid = col.replace("#", "")
        lines.append(
            f'<marker id="arrow_{cid}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7.5" markerHeight="7.5" orient="auto-start-reverse">'
            f'<path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="{col}"/>'
            f"</marker>"
        )
    lines.append("</defs>")

    lines.append(f'<rect x="0" y="0" width="{diag.width}" height="{diag.height}" fill="#FFFFFF"/>')

    # Header
    lines.append(
        f'<text x="36" y="42" font-size="24" font-weight="700" fill="#0F172A">{html.escape(diag.title)}</text>'
    )
    lines.append(
        f'<text x="36" y="66" font-size="14" fill="#475569">{html.escape(diag.subtitle)}</text>'
    )

    # Legend pills top-right
    if diag.legend:
        lx = diag.width - 36
        for lbl, pal_name in reversed(diag.legend):
            pal = PALETTES[pal_name]
            tw = max(92, int(len(lbl) * 7.2 + 24))
            lx -= tw + 10
            lines.append(
                f'<rect x="{lx}" y="22" width="{tw}" height="26" rx="13" '
                f'fill="{pal.fill}" stroke="{pal.stroke}" stroke-width="1.5"/>'
            )
            lines.append(
                f'<text x="{lx + tw / 2:.1f}" y="39" font-size="11.5" font-weight="600" '
                f'text-anchor="middle" fill="{pal.text}">{html.escape(lbl)}</text>'
            )

    # Groups
    for g in diag.groups:
        pal = PALETTES[g.palette]
        hh = 36
        lines.append(
            f'<rect x="{g.x}" y="{g.y}" width="{g.w}" height="{g.h}" rx="12" '
            f'fill="{pal.fill}" stroke="{pal.stroke}" stroke-width="1.8"/>'
        )
        lines.append(
            f'<path d="M {g.x},{g.y + hh} L {g.x},{g.y + 12} Q {g.x},{g.y} {g.x + 12},{g.y} '
            f'L {g.x + g.w - 12},{g.y} Q {g.x + g.w},{g.y} {g.x + g.w},{g.y + 12} '
            f'L {g.x + g.w},{g.y + hh} Z" fill="{pal.header_fill}" stroke="{pal.stroke}" stroke-width="1.2"/>'
        )
        gt_size = _fit_font_size(g.title, 14.0, g.w - 28, bold=True)
        lines.append(
            f'<text x="{g.x + 16}" y="{g.y + 23}" font-size="{gt_size:.1f}" font-weight="700" fill="{pal.text}">'
            f"{html.escape(g.title)}</text>"
        )

    # Nodes
    for n in diag.nodes:
        pal = PALETTES[n.palette]
        rx = n.h // 2 if n.shape == "pill" else 10
        if n.shape == "cylinder":
            lines.append(
                f'<rect x="{n.x}" y="{n.y + 7}" width="{n.w}" height="{n.h - 7}" rx="10" '
                f'fill="{pal.fill}" stroke="{pal.stroke}" stroke-width="1.8" filter="url(#cardShadow)"/>'
            )
            lines.append(
                f'<ellipse cx="{n.x + n.w/2:.1f}" cy="{n.y + 10}" rx="{n.w/2:.1f}" ry="7.5" '
                f'fill="{pal.header_fill}" stroke="{pal.stroke}" stroke-width="1.5"/>'
            )
        else:
            lines.append(
                f'<rect x="{n.x}" y="{n.y}" width="{n.w}" height="{n.h}" rx="{rx}" '
                f'fill="{pal.fill}" stroke="{pal.stroke}" stroke-width="1.8" filter="url(#cardShadow)"/>'
            )
            if n.shape != "pill" and n.palette != "dark":
                lines.append(
                    f'<rect x="{n.x}" y="{n.y}" width="7" height="{n.h}" rx="3.5" fill="{pal.stroke}"/>'
                )

        cx = n.x + (n.w + 5) / 2 if n.shape not in ("pill", "cylinder") and n.palette != "dark" else n.x + n.w / 2
        cy = n.y + (n.h + 8) / 2 if n.shape == "cylinder" else n.y + n.h / 2
        avail_w = n.w - 28

        t_size = _fit_font_size(n.title, 15.0, avail_w, bold=True)
        if n.subtitle:
            s_size = _fit_font_size(n.subtitle, 12.0, avail_w, bold=False)
            lines.append(
                f'<text x="{cx:.1f}" y="{cy - 6:.1f}" text-anchor="middle" font-size="{t_size:.1f}" '
                f'font-weight="700" fill="{pal.text}">{html.escape(n.title)}</text>'
            )
            lines.append(
                f'<text x="{cx:.1f}" y="{cy + 15:.1f}" text-anchor="middle" font-size="{s_size:.1f}" '
                f'fill="{pal.subtext}">{html.escape(n.subtitle)}</text>'
            )
        else:
            lines.append(
                f'<text x="{cx:.1f}" y="{cy + 5:.1f}" text-anchor="middle" font-size="{t_size:.1f}" '
                f'font-weight="700" fill="{pal.text}">{html.escape(n.title)}</text>'
            )

    # Edges
    for e in diag.edges:
        s_node = node_map[e.src]
        t_node = node_map[e.dst]
        p0 = _anchor_point(s_node, e.exit_pt)
        p1 = _anchor_point(t_node, e.entry_pt)
        pts = _orth_path(p0, p1, e.exit_pt, e.entry_pt, e.waypoints)
        d = _svg_rounded_polyline(pts, radius=10.0)
        cid = e.color.replace("#", "")
        sw = "2.4" if e.thick else "1.8"
        dash = ' stroke-dasharray="7 5"' if e.dashed else ""
        start_m = f' marker-start="url(#arrow_{cid})"' if e.bidirectional else ""
        lines.append(
            f'<path d="{d}" fill="none" stroke="{e.color}" stroke-width="{sw}"{dash}'
            f'{start_m} marker-end="url(#arrow_{cid})"/>'
        )

        if e.label:
            lx, ly = (float(e.label_pos[0]), float(e.label_pos[1])) if e.label_pos else _midpoint_of_polyline(pts)
            lbl_w = max(44, len(e.label) * 6.8 + 16)
            lines.append(
                f'<rect x="{lx - lbl_w/2:.1f}" y="{ly - 10:.1f}" width="{lbl_w:.1f}" height="19" '
                f'rx="5" fill="#FFFFFF" fill-opacity="0.96" stroke="{e.color}" stroke-opacity="0.45" stroke-width="1"/>'
            )
            lines.append(
                f'<text x="{lx:.1f}" y="{ly + 3.5:.1f}" text-anchor="middle" font-size="11.5" '
                f'font-weight="600" fill="{e.color}">{html.escape(e.label)}</text>'
            )

    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Diagram 1: High-Level Neuro-Symbolic Pipeline DAG (9 nodes)
# ---------------------------------------------------------------------------

def make_pipeline_dag() -> Diagram:
    groups = [
        Group("g_in", "Inputs", 36, 96, 340, 680, "indigo"),
        Group("g_pipe", "Neuro-Symbolic Generation Pipeline", 436, 96, 1028, 680, "emerald"),
    ]

    nodes = [
        # Inputs column (x=66, w=280)
        Node("in_prompt", "Text Prompt", "Natural-language level description", 66, 170, 280, 96, "indigo"),
        Node("in_profile", "Game Profile", "Physics & player abilities", 66, 360, 280, 96, "indigo"),
        Node("in_rag", "RAG & Design Memory", "Real level patterns & user preferences", 66, 550, 280, 96, "indigo"),

        # Main 2x3 flow inside pipeline zone
        Node("s1_llm", "1. LLM Spec & Symbolic Patcher", "Cloud / Local LLM → validated LevelSpec", 476, 170, 400, 104, "purple"),
        Node("s2_comp", "2. Completeness & Mission Graph", "Auto-fill missing items & key-lock loops", 476, 355, 400, 104, "amber"),
        Node("s3_terr", "3. Terrain Carving & RAG Stamps", "Carve 7 room types from rock + stamp patterns", 476, 540, 400, 104, "teal"),

        Node("s4_ver", "4. Physics & Soft-Lock Verification", "Swept-hitbox jump oracle + Tarjan SCC proof", 1016, 170, 410, 104, "emerald"),
        Node("s_rep", "Auto-Repair Loop", "Add stepping stones, raise ceilings, move keys", 1016, 355, 410, 104, "rose"),
        Node("s5_exp", "5. Comfort, Autotile & LDtk Export", "Short hops → CC0 autotiling → reload in LDtk", 1016, 540, 410, 104, "dark"),
    ]

    edges = [
        Edge("e_in1", "in_prompt", "s1_llm", "", (1.0, 0.5), (0.0, 0.5), "#4F46E5"),
        Edge("e_in2", "in_profile", "s4_ver", "physics bounds", (1.0, 0.5), (0.0, 0.75), "#4F46E5", dashed=True,
             waypoints=[(406, 408), (406, 725), (945, 725), (945, 248)], label_pos=(680, 725), thick=False),
        Edge("e_in3", "in_rag", "s3_terr", "patterns & prefs", (1.0, 0.5), (0.0, 0.5), "#4F46E5", dashed=True,
             label_pos=(406, 598), thick=False),

        Edge("e_1_2", "s1_llm", "s2_comp", "LevelSpec", (0.5, 1.0), (0.5, 0.0), "#7C3AED", label_pos=(676, 314)),
        Edge("e_2_3", "s2_comp", "s3_terr", "mission plan", (0.5, 1.0), (0.5, 0.0), "#0D9488", label_pos=(676, 498)),
        Edge("e_3_4", "s3_terr", "s4_ver", "tile grid", (1.0, 0.35), (0.0, 0.5), "#059669",
             waypoints=[(945, 576), (945, 222)], label_pos=(945, 408)),

        Edge("e_4_rep", "s4_ver", "s_rep", "if invalid", (0.35, 1.0), (0.35, 0.0), "#E11D48", dashed=True,
             label_pos=(1159, 314)),
        Edge("e_rep_4", "s_rep", "s4_ver", "re-verify", (0.65, 0.0), (0.65, 1.0), "#059669", dashed=True,
             label_pos=(1283, 314)),
        Edge("e_4_5", "s4_ver", "s5_exp", "verified", (1.0, 0.5), (1.0, 0.5), "#0F172A",
             waypoints=[(1446, 222), (1446, 592)], label_pos=(1446, 408)),
    ]

    legend = [
        ("Inputs", "indigo"),
        ("Neural LLM", "purple"),
        ("Mission / Fill", "amber"),
        ("Carving / Verify", "emerald"),
        ("Auto-Repair", "rose"),
        ("LDtk Output", "dark"),
    ]

    return Diagram(
        id="pipeline_dag",
        name="1 - Pipeline DAG",
        title="GLDtk: High-Level Neuro-Symbolic Pipeline DAG",
        subtitle="From natural-language prompt to statically verified, playable LDtk levels",
        width=1500,
        height=820,
        groups=groups,
        nodes=nodes,
        edges=edges,
        legend=legend,
    )


# ---------------------------------------------------------------------------
# Diagram 2: High-Level System Architecture (3 columns, 8 nodes)
# ---------------------------------------------------------------------------

def make_system_architecture() -> Diagram:
    groups = [
        Group("g_ed", "LDtk 1.5.3 Editor · Haxe UI", 36, 96, 430, 680, "indigo"),
        Group("g_be", "FastAPI Backend · 127.0.0.1:8765", 535, 96, 430, 680, "emerald"),
        Group("g_ext", "AI Providers & Local Storage", 1034, 96, 430, 680, "slate"),
    ]

    nodes = [
        # Column 1
        Node("ed_side", "GLDtk Sidebar", "Prompt, Fidelity Report & Difficulty chart", 66, 175, 370, 120, "indigo"),
        Node("ed_edit", "Regional Edit & Undo", "Area lasso, Section edit & 30-step Undo", 66, 375, 370, 120, "indigo"),
        Node("ed_play", "Route Overlay & Play Mode", "Canvas golden path & 60 Hz playable modal", 66, 575, 370, 120, "indigo"),

        # Column 2
        Node("be_gen", "Generation & Edit Engine", "Level, World, Chunk & Seam-Locked Edit pipelines", 565, 240, 370, 145, "teal"),
        Node("be_ver", "Symbolic Verifier & Oracle", "Kinematics, Soft-Lock SCC solver & Fidelity checker", 565, 480, 370, 145, "emerald"),

        # Column 3
        Node("ext_llm", "Cloud & Local LLMs", "Gemini / Claude with automatic Ollama Qwen fallback", 1064, 175, 370, 120, "purple"),
        Node("ext_rag", "Pattern RAG & Memory Store", "3,577 open-licensed patterns & learned user rules", 1064, 375, 370, 120, "blue", shape="cylinder"),
        Node("ext_disk", "LDtk Project Files", "*.ldtk · gldtk.game.json · CC0 tilesets & sprites", 1064, 575, 370, 120, "dark", shape="cylinder"),
    ]

    edges = [
        Edge("e_s_g", "ed_side", "be_gen", "POST /generate", (1.0, 0.5), (0.0, 0.35), "#2563EB",
             waypoints=[(500, 235), (500, 290)], label_pos=(500, 260)),
        Edge("e_e_g", "ed_edit", "be_gen", "POST /edit/* · /undo", (1.0, 0.5), (0.0, 0.75), "#0D9488",
             waypoints=[(500, 435), (500, 349)], label_pos=(500, 405)),
        Edge("e_p_v", "ed_play", "be_ver", "GET /route · /difficulty", (1.0, 0.5), (0.0, 0.6), "#059669",
             waypoints=[(500, 635), (500, 567)], label_pos=(500, 608)),

        Edge("e_g_v", "be_gen", "be_ver", "verify & repair", (0.5, 1.0), (0.5, 0.0), "#059669", bidirectional=True,
             label_pos=(750, 432)),

        Edge("e_g_llm", "be_gen", "ext_llm", "LevelSpec JSON", (1.0, 0.3), (0.0, 0.5), "#7C3AED",
             waypoints=[(1000, 283), (1000, 235)], label_pos=(1000, 250)),
        Edge("e_g_rag", "be_gen", "ext_rag", "retrieve & learn", (1.0, 0.7), (0.0, 0.5), "#2563EB", dashed=True,
             waypoints=[(1000, 341), (1000, 435)], label_pos=(1000, 392), thick=False),
        Edge("e_v_disk", "be_ver", "ext_disk", "verified write", (1.0, 0.5), (0.0, 0.35), "#0F172A",
             waypoints=[(1000, 552), (1000, 617)], label_pos=(1000, 588)),
        Edge("e_disk_ed", "ext_disk", "ed_play", "hot-reload", (0.5, 1.0), (0.5, 1.0), "#334155", dashed=True,
             waypoints=[(1249, 735), (251, 735)], label_pos=(750, 735), thick=False),
    ]

    legend = [
        ("LDtk Haxe UI", "indigo"),
        ("Backend Engine", "emerald"),
        ("LLMs", "purple"),
        ("RAG & Memory", "blue"),
        ("Project Files", "dark"),
    ]

    return Diagram(
        id="system_arch",
        name="2 - System Architecture",
        title="GLDtk: High-Level System Architecture",
        subtitle="Native LDtk 1.5.3 Haxe UI bridged to the local FastAPI neuro-symbolic engine",
        width=1500,
        height=820,
        groups=groups,
        nodes=nodes,
        edges=edges,
        legend=legend,
    )


# ---------------------------------------------------------------------------
# Diagram 3: High-Level Verification & Co-Creation Flows (3 rows, 9 nodes)
# ---------------------------------------------------------------------------

def make_verification_dags() -> Diagram:
    groups = [
        Group("g_kin", "1. Kinematic Reachability Proof", 36, 96, 1428, 200, "emerald"),
        Group("g_soft", "2. Global Soft-Lock Freedom Proof", 36, 326, 1428, 200, "rose"),
        Group("g_edit", "3. Non-Destructive Regional Editing", 36, 556, 1428, 200, "teal"),
    ]

    nodes = [
        # Row 1: Kinematic proof
        Node("k1", "Extract Platform Graph", "Standable spans & 2-tile headroom", 76, 160, 400, 102, "teal"),
        Node("k2", "Kinematic Arc Solver", "Single/Double jump, Dash, Wall kick & Gravity", 550, 160, 400, 102, "emerald"),
        Node("k3", "Swept Hitbox Check", "12×24 px collision-free Start → Exit path", 1024, 160, 400, 102, "blue"),

        # Row 2: Soft-lock proof
        Node("s1", "Movement & Item Graph", "Platform digraph + Key/Door inventory states", 76, 390, 400, 102, "slate"),
        Node("s2", "Tarjan SCC & Deadlock Check", "Every reachable state can still reach Exit", 550, 390, 400, 102, "rose"),
        Node("s3", "Deterministic Auto-Repair", "Insert escape ledges or relocate trapped keys", 1024, 390, 400, 102, "amber"),

        # Row 3: Regional editing
        Node("r1", "Select Region or Section", "Keep all manual edits outside selection", 76, 620, 400, 102, "teal"),
        Node("r2", "Lock Border Seams", "Freeze entry & exit crossings on boundary", 550, 620, 400, 102, "amber"),
        Node("r3", "Splice & Re-Verify Whole Level", "Save undo snapshot & hot-reload in LDtk", 1024, 620, 400, 102, "dark"),
    ]

    edges = [
        Edge("ek1", "k1", "k2", "spans", (1.0, 0.5), (0.0, 0.5), "#059669", label_pos=(513, 211)),
        Edge("ek2", "k2", "k3", "arcs", (1.0, 0.5), (0.0, 0.5), "#2563EB", label_pos=(987, 211)),

        Edge("es1", "s1", "s2", "states", (1.0, 0.5), (0.0, 0.5), "#E11D48", label_pos=(513, 441)),
        Edge("es2", "s2", "s3", "if trapped", (1.0, 0.5), (0.0, 0.5), "#D97706", dashed=True, label_pos=(987, 441)),
        Edge("es3", "s3", "s1", "re-check", (0.5, 0.0), (0.5, 0.0), "#059669", dashed=True,
             waypoints=[(1224, 368), (276, 368)], label_pos=(750, 368), thick=False),

        Edge("er1", "r1", "r2", "bounds", (1.0, 0.5), (0.0, 0.5), "#0D9488", label_pos=(513, 671)),
        Edge("er2", "r2", "r3", "local edit", (1.0, 0.5), (0.0, 0.5), "#0F172A", label_pos=(987, 671)),
    ]

    legend = [
        ("Kinematics", "emerald"),
        ("Soft-Lock Proof", "rose"),
        ("Regional Edit", "teal"),
        ("Repair / Seams", "amber"),
        ("Guaranteed Write", "dark"),
    ]

    return Diagram(
        id="verification_dags",
        name="3 - Verification & Co-Creation",
        title="GLDtk: Verification Guarantees & Mixed-Initiative Editing Flows",
        subtitle="High-level view of kinematic proofs, soft-lock safety, and seam-locked editing",
        width=1500,
        height=800,
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
