#!/usr/bin/env python3
"""Print how many dialogue lines each category has and how many combinations
the generated text (diary, adventures, trinkets, rival names) can make.

    python3 scripts/line_stats.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet import stories  # noqa: E402
from pet.speech import load_pools  # noqa: E402


def main() -> int:
    pools = load_pools()
    groups: dict[str, int] = defaultdict(int)
    for key, lines in pools.items():
        groups[key.split(".")[0]] += len(lines)
    total = sum(groups.values())
    print(f"{total} lines in {len(pools)} pools\n")
    print("Lines per category:")
    for name, count in sorted(groups.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<12} {count:>5}")
    print("\nCombinations of generated text:")
    for name, count in stories.combination_estimates().items():
        print(f"  {name:<20} {count:>24,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
