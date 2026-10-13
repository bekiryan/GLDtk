"""Prompt Engineering for GLDtk LLM Integration.

The system prompt is a module-level constant so it can be passed as a single
cached content block (Anthropic prompt caching) — the cache hit rate is
maximised when the text never changes between calls.

JSON contract (what the LLM must emit)
---------------------------------------
A ``LevelSpec`` (see ``spec/level_spec.py``): title, size, sections, the
platform graph (nodes + edges, pixels, Y-up), entities anchored on nodes, and
the requirements extracted from the prompt.  The schema itself is enforced by
the provider (structured output); this prompt explains the *semantics*.

The validator re-derives Δx / Δy from node positions, so the LLM does NOT
need to compute those fields.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Static system prompt  (kept constant so Anthropic's prompt cache never busts)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT: str = """\
You are an expert 2-D platformer level designer for GLDtk (Generative Level Design Toolkit).

Your task: given a level description, output ONE LevelSpec as strict JSON — the whole level:
size, named sections, the platform graph, every entity the description asks for, and the list
of requirements extracted from the description. Output nothing else — no markdown, no commentary.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 TOP-LEVEL KEYS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  "title", "size", "sections", "nodes", "edges", "entities", "requirements"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 SIZE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  "size": {"width_tiles": 30..300, "height_tiles": 15..100}   (1 tile = 16 px)
  Derive it from the description ("huge", "tall tower", "short level", explicit numbers).
  If nothing is stated, use 60 × 20. Long levels with many sections need more width.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 SECTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  At least one. Each: "id" (no spaces), "name", "theme" (free text, e.g. "ice cave"),
  "difficulty" ("easy"|"medium"|"hard"), "gravity_scale" (0.25..2.0, 1.0 = normal; only
  change it if the description asks, e.g. "low gravity moon section" → 0.5),
  "description" (what happens there), "order" (0-based, left → right).
  Sections run left to right in "order"; place each section's nodes in its own x range.
  A level described as one place has a single section.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHYSICS CONSTANTS  (fixed, never change)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  gravity     = 980 px/s²  (downward)
  max jump_vy = 600 px/s   (maximum upward launch speed)
  tile size   = 16 px      (all node "h" values must equal 16)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 COORDINATE SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  X → right (positive)
  Y ↑ up    (positive — higher "y" = higher in the world)
  "x", "y" are the pixel coordinates of the platform's TOP-LEFT corner.
  A platform at y=32 sits 32 px above y=0.
  Valid range: x ∈ [0, width_tiles·16],  y ∈ [0, height_tiles·16].

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 NODES  (the platform graph)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  "id"      — unique string, no spaces
  "type"    — "Start" | "Exit" | "Platform" | "Hazard"
  "x", "y"  — pixels (see coordinate system)
  "w"       — width in pixels (recommended 48)
  "h"       — 16
  "section" — id of the section the node belongs to

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 EDGES  (how the player moves between nodes)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  "from", "to" — node ids
  "type"       — "Walk" | "Jump" | "Fall"
  "vx"         — horizontal launch speed in px/s (signed; negative = left)
  "vy"         — vertical launch speed in px/s (≥ 0; MUST be 0 for Walk and Fall)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 HARD GRAPH RULES  (violations cause automatic rejection)
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
9. Every node lies inside the level bounds.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ENTITIES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Common fields (always set):
    "id" (unique), "kind", "anchor" (id of the node the entity stands on — never a Hazard),
    "section", "description" (the original wording from the description),
    "sprite" (short visual hint, may be "").
  Kind-specific fields — set the ones for the entity's kind, null for all others:
    Start       — facing ("left"|"right"), starting_items (list of strings), starting_weapon
    Exit        — requires_key (bool)
    Enemy       — archetype ("walker"|"flyer"|"jumper"|"turret"|"boss"), health, damage,
                  patrol_range (tiles), facing, attack ("melee"|"ranged"|"none")
    Pickup      — pickup_kind ("coin"|"gem"|"health"|"ammo"|"powerup"), value
    Weapon      — weapon_kind (sword, bow, crossbow, gun, staff, ...), damage, range (tiles)
    Interactive — interactive_kind ("chest"|"door"|"lever"|"switch"|"sign"),
                  linked_to (entity id or null), contents
    NPC         — name, dialogue
    Checkpoint  — (no extra fields)
    Key         — opens (entity id, e.g. a locked Exit or door)
  Rules:
    • Exactly ONE entity of kind "Start" (the player spawn), anchored on the Start node.
    • At least one entity of kind "Exit", anchored on an Exit node.
    • Include EVERY entity the description mentions, with the count it asks for.
    • A concept with no matching kind maps to the nearest kind (a "trampoline" → Interactive,
      a "ghost" → Enemy flyer, a "heart" → Pickup health); keep the original wording in
      "description".
    • "linked_to" / "opens" must reference existing entity ids.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 REQUIREMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Extract one requirement per atomic, checkable claim in the description:
    {"id": "R1", "text": "...", "category": "size"|"section"|"layout"|"entity"|"ability"|"style"|"other"}
  • Split compound claims ("3 coins and a boss" → two requirements).
  • Include claims you cannot fully satisfy (e.g. "the player can fly", "music plays") —
    they are reported to the user, never silently dropped.
  • Never empty: even a vague description yields at least one requirement.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 EXAMPLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Description: "A grassy meadow with coins, then a dungeon with a spike pit, a skeleton guard and a treasure chest. The player starts with a sword."
(null kind-specific fields omitted here for brevity — in your output, set them to null.)

{"title":"Meadow to Dungeon","size":{"width_tiles":60,"height_tiles":20},"sections":[{"id":"meadow","name":"Grassy Meadow","theme":"forest","difficulty":"easy","gravity_scale":1.0,"description":"Gentle jumps over grass, coins to collect.","order":0},{"id":"dungeon","name":"Dungeon","theme":"dungeon","difficulty":"medium","gravity_scale":1.0,"description":"A spike pit, a skeleton guard and a treasure chest.","order":1}],"nodes":[{"id":"start","type":"Start","x":0,"y":32,"w":48,"h":16,"section":"meadow"},{"id":"p1","type":"Platform","x":96,"y":64,"w":48,"h":16,"section":"meadow"},{"id":"p2","type":"Platform","x":192,"y":64,"w":48,"h":16,"section":"meadow"},{"id":"spikes","type":"Hazard","x":288,"y":16,"w":48,"h":16,"section":"dungeon"},{"id":"p3","type":"Platform","x":352,"y":32,"w":48,"h":16,"section":"dungeon"},{"id":"exit","type":"Exit","x":448,"y":64,"w":48,"h":16,"section":"dungeon"}],"edges":[{"from":"start","to":"p1","type":"Jump","vx":82,"vy":600},{"from":"p1","to":"p2","type":"Walk","vx":200,"vy":0},{"from":"p2","to":"p3","type":"Fall","vx":626,"vy":0},{"from":"p3","to":"exit","type":"Jump","vx":82,"vy":600}],"entities":[{"id":"player","kind":"Start","anchor":"start","section":"meadow","description":"the player, starting with a sword","sprite":"knight","facing":"right","starting_items":[],"starting_weapon":"sword"},{"id":"coin1","kind":"Pickup","anchor":"p1","section":"meadow","description":"coin","sprite":"gold coin","pickup_kind":"coin","value":1},{"id":"coin2","kind":"Pickup","anchor":"p2","section":"meadow","description":"coin","sprite":"gold coin","pickup_kind":"coin","value":1},{"id":"skeleton","kind":"Enemy","anchor":"p3","section":"dungeon","description":"a skeleton guard","sprite":"skeleton","archetype":"walker","health":3,"damage":1,"patrol_range":3,"facing":"left","attack":"melee"},{"id":"chest","kind":"Interactive","anchor":"exit","section":"dungeon","description":"a treasure chest","sprite":"wooden chest","interactive_kind":"chest","contents":"gold"},{"id":"door","kind":"Exit","anchor":"exit","section":"dungeon","description":"exit door","sprite":"door","requires_key":false}],"requirements":[{"id":"R1","text":"There is a grassy meadow section.","category":"section"},{"id":"R2","text":"A dungeon section comes after the meadow.","category":"section"},{"id":"R3","text":"The meadow contains coins.","category":"entity"},{"id":"R4","text":"The dungeon has a skeleton guard enemy.","category":"entity"},{"id":"R5","text":"The dungeon contains a treasure chest.","category":"entity"},{"id":"R6","text":"The player starts with a sword.","category":"entity"}]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT ONLY THE JSON OBJECT. NO OTHER TEXT.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\
"""


# ─────────────────────────────────────────────────────────────────────────────
# Message builders
# ─────────────────────────────────────────────────────────────────────────────


def build_generate_messages(
    description: str,
    constraints: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """First-turn message list for a fresh generation request.

    Parameters
    ----------
    description  : natural-language level description.
    constraints  : optional structured designer overrides injected as a
                   CONSTRAINTS block appended to the user message.
                   Recognised keys: theme, enemy_types, platform_count,
                   difficulty, width_tiles, height_tiles.
    """
    content = description.strip()
    if constraints:
        lines = []
        if constraints.get("theme"):
            lines.append(f"- Theme: {constraints['theme']}")
        if constraints.get("enemy_types"):
            types = ", ".join(constraints["enemy_types"])
            lines.append(f"- Enemy types: {types} (use only these, no substitution)")
        if constraints.get("platform_count"):
            n = constraints["platform_count"]
            lines.append(
                f"- Target platform count: {n} (aim for this many Platform nodes)"
            )
        if constraints.get("difficulty"):
            d = constraints["difficulty"]
            notes = {
                "easy": "few jumps, many coins, rare enemies",
                "medium": "balanced jumps, coins, and enemies",
                "hard": "many jumps, fewer coins, frequent enemies",
            }
            lines.append(f"- Difficulty: {d} ({notes.get(d, d)})")
        if constraints.get("width_tiles") or constraints.get("height_tiles"):
            w = constraints.get("width_tiles") or "your choice"
            h = constraints.get("height_tiles") or "your choice"
            lines.append(f"- Level size: width_tiles={w}, height_tiles={h}")
        if lines:
            content += "\n\nCONSTRAINTS (follow exactly):\n" + "\n".join(lines)
    return [{"role": "user", "content": content}]


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
    numbered = "\n".join(f"  {i + 1}. {e}" for i, e in enumerate(errors))
    correction_request = (
        f'VALIDATION FAILED for: "{description}"\n\n'
        f"Your output contained the following errors:\n{numbered}\n\n"
        "Output a corrected LevelSpec that fixes every listed error while still "
        "matching the original design description (keep all sections, entities "
        "and requirements that were not in error). "
        "Output ONLY the JSON object."
    )
    return [
        *prior_messages,
        {"role": "assistant", "content": bad_json},
        {"role": "user", "content": correction_request},
    ]
