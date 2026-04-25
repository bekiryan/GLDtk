"""Prompt Engineering for GLDtk LLM Integration.

The system prompt is a module-level constant so it can be passed as a single
cached content block (Anthropic prompt caching) — the cache hit rate is
maximised when the text never changes between calls.

JSON contract (what the LLM must emit)
---------------------------------------
{
  "nodes": [
    { "id": str, "type": "Start"|"Exit"|"Platform"|"Hazard",
      "x": float,   // left edge in pixels  (Y-UP coord system)
      "y": float,   // top-surface in pixels (higher → physically higher)
      "w": float,   // width  (recommend 48)
      "h": float    // height (always 16) }
  ],
  "edges": [
    { "from": str, "to": str,
      "type": "Walk"|"Jump"|"Fall",
      "vx": float,  // horizontal launch speed (px/s, signed)
      "vy": float   // upward launch speed  (px/s; 0 for Walk and Fall) }
  ]
}

The validator re-derives Δx / Δy from node positions, so the LLM does NOT
need to compute those fields.
"""

from __future__ import annotations

from typing import Any, Dict, List

# ─────────────────────────────────────────────────────────────────────────────
# Static system prompt  (kept constant so Anthropic's prompt cache never busts)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT: str = """\
You are an expert 2-D platformer level designer for GLDtk (Generative Level Design Toolkit).

Your sole task: given a level description, output ONE valid Abstract Level Graph as strict JSON.
Output nothing else — no markdown fences, no explanation, no commentary.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHYSICS CONSTANTS  (fixed, never change)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  gravity     = 980 px/s²  (downward)
  max jump_vy = 600 px/s   (maximum upward launch speed)
  tile size   = 16 px      (all "h" values must equal 16)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 COORDINATE SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  X → right (positive)
  Y ↑ up    (positive — higher "y" = higher in the world)
  "x", "y" are the pixel coordinates of the platform's TOP-LEFT corner.
  A platform at y=32 sits 32 px above y=0.
  Valid range: x ∈ [0, 960],  y ∈ [0, 320].

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Output only a single JSON object with two top-level keys: "nodes" and "edges".

NODE fields:
  "id"   — unique string identifier, no spaces
  "type" — exactly one of: "Start", "Exit", "Platform", "Hazard"
  "x"    — float, left edge of platform in pixels
  "y"    — float, top-surface height in pixels (Y-UP)
  "w"    — float, width in pixels  (recommended: 48)
  "h"    — float, height in pixels (must be 16)

EDGE fields:
  "from" — source node "id"
  "to"   — target node "id"
  "type" — exactly one of: "Walk", "Jump", "Fall"
  "vx"   — horizontal launch speed in px/s (signed; negative = left)
  "vy"   — vertical   launch speed in px/s (≥ 0; MUST be 0 for Walk and Fall)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 HARD RULES  (violations cause automatic rejection)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Exactly one node with "type": "Start".
2. At least one node with "type": "Exit".
3. There MUST be an unbroken edge path from Start to every Exit.
4. Jump edges:
     • target.y ≥ source.y  (you jump UP or level; never set Jump for a downward move)
     • max height: Δy = target.y − source.y ≤ 183 px  (physics ceiling)
     • vy must equal 600 (always launch at full jump speed)
     • vx = Δx / t*, where t* = (600 + √(600²−2·980·Δy)) / 980
       → for Δy=0: vx ≈ Δx/1.224;  for Δy=32: vx ≈ Δx/1.169;
         for Δy=64: vx ≈ Δx/1.108;  for Δy=128: vx ≈ Δx/0.969
5. Fall edges:
     • target.y < source.y  (strictly downward)
     • vy = 0
     • vx = Δx / t_fall, where t_fall = √(2·|Δy|/980)
       → for |Δy|=32: t≈0.256 s;  for |Δy|=64: t≈0.361 s
6. Walk edges:
     • |target.y − source.y| ≤ 8 px  (nearly level ground)
     • vy = 0,  |vx| ∈ [150, 300]
7. Hazard nodes must NOT have outgoing edges (players cannot stand on hazards).
8. Self-loops are forbidden.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 EXAMPLE 1 — simple linear level
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Description: "A short level. Player starts on the ground, jumps to a raised platform, walks to the exit."

{"nodes":[{"id":"start","type":"Start","x":0,"y":0,"w":48,"h":16},{"id":"p1","type":"Platform","x":80,"y":32,"w":48,"h":16},{"id":"exit","type":"Exit","x":128,"y":32,"w":48,"h":16}],"edges":[{"from":"start","to":"p1","type":"Jump","vx":69,"vy":600},{"from":"p1","to":"exit","type":"Walk","vx":200,"vy":0}]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 EXAMPLE 2 — medium level with hazard and fall
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Description: "A cave with a spike pit. Player jumps up to a high ledge, falls across the spikes below, then jumps up to the exit."

{"nodes":[{"id":"start","type":"Start","x":0,"y":48,"w":48,"h":16},{"id":"p1","type":"Platform","x":80,"y":80,"w":48,"h":16},{"id":"spikes","type":"Hazard","x":80,"y":48,"w":48,"h":16},{"id":"p2","type":"Platform","x":144,"y":48,"w":48,"h":16},{"id":"exit","type":"Exit","x":224,"y":80,"w":48,"h":16}],"edges":[{"from":"start","to":"p1","type":"Jump","vx":69,"vy":600},{"from":"p1","to":"p2","type":"Fall","vx":250,"vy":0},{"from":"p2","to":"exit","type":"Jump","vx":69,"vy":600}]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT ONLY THE JSON OBJECT. NO OTHER TEXT.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\
"""


# ─────────────────────────────────────────────────────────────────────────────
# Message builders
# ─────────────────────────────────────────────────────────────────────────────

def build_generate_messages(description: str) -> List[Dict[str, Any]]:
    """First-turn message list for a fresh generation request."""
    return [{"role": "user", "content": description.strip()}]


def build_repair_messages(
    description: str,
    bad_json: str,
    errors: List[str],
    prior_messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Append the LLM's bad output + a correction request to the conversation.

    Keeps the full conversation history so the model sees exactly what it
    produced and what was wrong — far more informative than a fresh prompt.
    """
    numbered = "\n".join(f"  {i+1}. {e}" for i, e in enumerate(errors))
    correction_request = (
        f"VALIDATION FAILED for: \"{description}\"\n\n"
        f"Your output contained the following errors:\n{numbered}\n\n"
        "Output a corrected graph that fixes every listed error while still "
        "matching the original design description. "
        "Output ONLY the JSON object."
    )
    return [
        *prior_messages,
        {"role": "assistant", "content": bad_json},
        {"role": "user",      "content": correction_request},
    ]
