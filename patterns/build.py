"""Build the pattern index: ``python -m patterns.build``.

Ingests LDtk's samples (and any ``--add`` files / the demo folders), labels the accepted
patterns with a budget, embeds them and prints the counts.  Never run it with a real
provider from tests; ``--dry-run`` makes no LLM call.

    python -m patterns.build --dry-run
    python -m patterns.build --provider gemini --max-calls 40 --embedder gemini
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List, Optional

from patterns.embed import make_embedder, sync_vectors
from patterns.ingest import OPEN_CORPORA_HOOK, add_path, ingest_samples
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
    print(f"  ({OPEN_CORPORA_HOOK.split(':')[0]}: not ingested)")

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
