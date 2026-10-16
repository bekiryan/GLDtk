"""Build the pattern index: ``python -m patterns.build``.

Ingests LDtk's samples (and any ``--add`` files / the demo folders), labels the accepted
patterns with a budget, embeds them and prints the counts.  Never run it with a real
provider from tests; ``--dry-run`` makes no LLM call.

    python -m patterns.build --dry-run
    python -m patterns.build --provider gemini --max-calls 40 --embedder gemini
    python -m patterns.build --source all --max-levels 40 \\
        --provider ollama --model qwen2.5:14b --max-calls 150 --embedder ollama

``--source`` (Part 26d) shallow-clones the game into the app-data ``sources/`` folder when it is
missing, checks each level's licence, converts and ingests it (see ``patterns.sources``).
Design notes are built separately: ``python -m patterns.notes``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List, Optional

from patterns.embed import make_embedder, sync_vectors
from patterns.ingest import add_path, ingest_samples
from patterns.label import label_pending
from patterns.store import PatternStore

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIRS = [
    ROOT / "ldtk-src" / "app" / "extraFiles" / "samples",
    Path("/Users/bekiryan/Projects/GLDtk/ldtk-src/app/extraFiles/samples"),
]


def default_samples_dir() -> Optional[Path]:
    env = os.environ.get("GLDTK_SAMPLES_DIR")
    for p in ([Path(env)] if env else []) + SAMPLE_DIRS:
        if p.is_dir():
            return p
    return None


def demo_files() -> List[Path]:
    """``.ldtk`` files in the repo's demo folders (``demo*/``), if any."""
    return sorted(p for d in ROOT.glob("demo*") if d.is_dir() for p in d.rglob("*.ldtk"))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m patterns.build", description=__doc__.split("\n")[0])
    ap.add_argument("--samples", type=Path, default=None, help="folder of sample .ldtk files")
    ap.add_argument("--add", type=Path, action="append", default=[], help="an extra .ldtk to ingest")
    ap.add_argument("--embedder", default="hash", help="hash | gemini | ollama")
    ap.add_argument("--provider", default=None, help="label with this LLM provider (omit: no labelling)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-calls", type=int, default=None, help="cap on labelling LLM calls")
    ap.add_argument("--dry-run", action="store_true", help="ingest and count, no LLM call, nothing saved")
    ap.add_argument("--source", choices=["supertux", "opensurge", "frogatto", "all"], default=None,
                    help="also download (if missing), convert and ingest an open-licensed game")
    ap.add_argument("--max-levels", type=int, default=None, help="cap on converted levels per game")
    args = ap.parse_args(argv)

    store = PatternStore()
    samples = args.samples or default_samples_dir()
    if samples:
        rep = ingest_samples(store, samples)
    else:
        from patterns.ingest import IngestReport

        rep = IngestReport()
        print("no samples folder found (set GLDTK_SAMPLES_DIR or --samples)")
    for p in demo_files() + list(args.add):
        rep.merge(add_path(store, p, source="gldtk", license="project-owned (GLDtk)"))
    print(f"ingest: {rep.added} new, {rep.duplicates} already indexed, {rep.rejected} rejected "
          f"(no Start->Exit route)")
    for name, n in sorted(rep.mapped_files.items()):
        print(f"  mapped   {name}: {n}")
    for name, why in sorted(rep.skipped_files.items()):
        print(f"  skipped  {name}: {why}")
    if args.source:
        from patterns.sources import GAMES, ingest_source

        for game in (GAMES if args.source == "all" else [args.source]):
            srep, irep = ingest_source(store, game, max_levels=args.max_levels)
            print(f"source {game}: {len(srep.included)} level file(s) included {srep.licenses}, "
                  f"{irep.added} new pattern(s) ({irep.rejected} rejected: no route), "
                  f"{irep.duplicates} already indexed, {irep.levels_added} whole level(s)")
            for name, n in sorted(irep.mapped_files.items()):
                print(f"  level    {name}: {n}")
            for name, why in sorted(srep.skipped.items()):
                print(f"  skipped  {name}: {why}")
            if not args.dry_run:
                store.save()
    else:
        print(f"  (open-licensed game levels: --source supertux|opensurge|frogatto|all)")

    lrep = None
    if args.provider or args.dry_run:
        provider = None
        if args.provider and not args.dry_run:
            from llm.providers import make_provider

            provider = make_provider(args.provider, model=args.model)
        lrep = label_pending(store, provider, max_calls=args.max_calls, dry_run=args.dry_run)
        print(f"label: {lrep.labelled} new, {lrep.cached} cached, {lrep.pending} pending, "
              f"{lrep.calls} call(s)" + (" [dry run]" if args.dry_run else "")
              + (" [budget reached]" if lrep.stopped else ""))
        for e in lrep.errors:
            print("  error:", e)
    if args.dry_run:
        print("dry run: nothing saved")
        return 0
    n = sync_vectors(store, make_embedder(args.embedder))
    store.save()
    print(f"embed: {n} new vector(s) with {store.embedder['name'] if store.embedder else '-'}")
    s = store.stats()
    print(f"index: {s['patterns']} patterns, {s['accepted']} accepted, {s['labelled']} labelled, "
          f"{s['embedded']} embedded -> {s['dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
