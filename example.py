"""Generate a level from a prompt without the LDtk app — the same code path as the sidebar.

    source .venv/bin/activate
    python example.py "A forest with 5 coins, then a dark cave with 2 bats and a spike pit"
    python example.py --provider ollama --model qwen2.5:14b "a small icy climb"

Writes ``example_out/level.ldtk`` (open it in LDtk) and prints the fidelity report.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import server  # loads .env (GEMINI_API_KEY) and wires the providers
from api.models import GenerateRequest

ICONS = {"satisfied": "OK ", "approximated": "~  ", "impossible": "X  ", "missing": "X  ", "unchecked": ".  "}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="?", default=(
        "A sunny forest with 5 coins and a slime, then a dark cave with 2 bats, a spike pit "
        "and a treasure chest, and finally a castle with a skeleton boss guarding the exit. "
        "The player starts with a sword."
    ))
    ap.add_argument("--provider", default="gemini", help="gemini | anthropic | ollama | openai | deepseek")
    ap.add_argument("--model", default=None, help="default: the provider's default model")
    ap.add_argument("--out", default="example_out/level.ldtk")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    out = Path(args.out).resolve()
    res = server.generate(GenerateRequest(
        description=args.prompt, output_path=str(out), seed=args.seed,
        provider=args.provider, model=args.model,
    ))
    if not res.success:
        print(f"failed: {res.error}")
        return 1

    print(res.preview_ascii)
    s = res.stats
    print(f"\n{s.theme}: {s.nodes} platforms, entities {s.entities}, verified={res.verified}")
    if res.fidelity:
        score = res.fidelity.score
        print(f"fidelity: {'n/a' if score is None else f'{score:.0%}'}")
        for item in res.fidelity.items:
            print(f"  {ICONS.get(item.status, '?  ')}{item.text}  ({item.reason})")
    for w in res.warnings:
        print(f"  warning: {w}")
    print(f"\nwrote {res.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
