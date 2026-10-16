"""Is a hand-built (or generated) .ldtk playable?  Offline, no LLM: GLDtk's own route check.

    .venv/bin/python demo/verify.py demo/out/manual_full/crypt.ldtk [--json out.json]

Prints whether Start reaches an Exit, the soft-lock spots and the required entities the player
cannot reach.  Exit code 0 = playable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from layout.route import file_route  # noqa: E402


def check(path: str) -> dict:
    try:
        r = file_route(path)
    except Exception as exc:  # an unreadable or half-built file is simply not playable
        return {"file": path, "playable": False, "error": str(exc)}
    d = r.as_dict()
    return {
        "file": path,
        "playable": bool(d.get("success")) and not d.get("softlock_spans"),
        "start_reaches_exit": bool(d.get("success")),
        "softlock_spots": len(d.get("softlock_spans") or []),
        "unreachable_required": len(d.get("unreachable_required") or []),
        "route_moves": len(d.get("steps") or []),
    }


def main(argv: list) -> int:
    res = check(argv[0])
    print(json.dumps(res, indent=1))
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0 if res["playable"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
