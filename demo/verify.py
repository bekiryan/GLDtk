"""Is a hand-built (or generated) .ldtk playable?  Offline, no LLM: GLDtk's own route check.

    .venv/bin/python demo/verify.py demo/out/manual_full/crypt.ldtk [--json out.json]

Prints whether Start reaches an Exit, the soft-lock spots and the required entities the player
cannot reach.  Exit code 0 = playable.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from layout.route import file_route  # noqa: E402


def with_rules_from(path: str, target: str) -> str:
    """A temp copy of *path* carrying *target*'s GLDtk level fields."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    tdoc = json.loads(Path(target).read_text(encoding="utf-8"))
    keep = [f for f in doc["levels"][0]["fieldInstances"] if not f["__identifier"].startswith("gldtk_")]
    doc["levels"][0]["fieldInstances"] = keep + [
        f for f in tdoc["levels"][0]["fieldInstances"] if f["__identifier"].startswith("gldtk_")]
    fd, tmp = tempfile.mkstemp(suffix=".ldtk", dir=str(Path(path).parent))
    with open(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return tmp


def check(path: str, rules_from: str | None = None) -> dict:
    src = with_rules_from(path, rules_from) if rules_from else path
    try:
        r = file_route(src)
    except Exception as exc:  # an unreadable or half-built file is simply not playable
        return {"file": path, "playable": False, "error": str(exc)}
    finally:
        if src != path:
            Path(src).unlink(missing_ok=True)
    d = r.as_dict()
    return {
        "file": path,
        "playable": bool(d.get("success")) and not d.get("softlock_spans"),
        "start_reaches_exit": bool(d.get("success")),
        "softlock_spots": len(d.get("softlock_spans") or []),
        "unreachable_required": len(d.get("unreachable_required") or []),
        "route_moves": len(d.get("steps") or []),
        "rules_from": rules_from,
    }


def main(argv: list) -> int:
    rules = argv[argv.index("--rules-from") + 1] if "--rules-from" in argv else None
    res = check(argv[0], rules)
    print(json.dumps(res, indent=1))
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0 if res["playable"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
