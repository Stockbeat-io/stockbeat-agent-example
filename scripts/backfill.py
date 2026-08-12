"""Backfill alpha checkpoints onto historical decisions.

Idempotent and safe to re-run: grading is write-once, so a checkpoint already
present is left alone and only newly-elapsed windows are added.

Features cannot be backfilled — the screening evidence behind past decisions was
never recorded — so historical records keep `features: null` and only decisions
made after this change support attribution analysis.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.market import excursion_since, window_return  # noqa: E402
from memory.memory import _read, _write, grade_open_decisions  # noqa: E402
from memory.review import agent_dirs  # noqa: E402


def migrate_legacy(path: Path) -> int:
    """Reopen decisions closed by the old one-day resolution scheme.

    That scheme graded every decision on the next run and marked it `resolved`,
    so a 60-day thesis was scored on a single day of noise. Those numbers are
    preserved as `legacy_outcome` for reference but are not treated as grades:
    the record is reopened so real checkpoints can land on it.
    """
    records = _read(path)
    migrated = 0
    for rec in records:
        if rec.get("status") != "resolved":
            continue
        if "outcome" in rec:
            rec["legacy_outcome"] = rec.pop("outcome")
        rec["status"] = "open"
        migrated += 1
    if migrated:
        _write(path, records)
    return migrated


def backfill(root: Path, run_date: str, dry_run: bool = False) -> dict:
    """Migrate then grade every agent's log.

    Returns {agent: {"migrated", "graded", "checkpoints"}}.
    """
    results = {}
    for directory in agent_dirs(root):
        path = directory / "memory" / "decisions.jsonl"
        if dry_run:
            records = _read(path)
            results[directory.name] = {
                "would_read": len(records),
                "would_migrate": sum(1 for r in records
                                     if r.get("status") == "resolved"),
            }
            continue
        migrated = migrate_legacy(path)
        stats = grade_open_decisions(
            path, window_fn=window_return, excursion_fn=excursion_since,
            run_date=run_date)
        results[directory.name] = {"migrated": migrated, **stats}
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path.home() / ".stockbeat-agent"))
    parser.add_argument("--run-date", default="9999-12-31",
                        help="Grade everything dated before this (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be read without writing")
    args = parser.parse_args(argv)

    root = Path(args.root)
    results = backfill(root, args.run_date, dry_run=args.dry_run)
    if not results:
        print(f"No decision logs found under {root}")
        return 1

    total_checkpoints = 0
    for agent, stats in sorted(results.items()):
        if args.dry_run:
            print(f"{agent:22} would read {stats['would_read']:>3} decisions, "
                  f"reopen {stats['would_migrate']:>3} legacy-resolved")
            continue
        total_checkpoints += stats["checkpoints"]
        print(f"{agent:22} {stats['migrated']:>3} reopened, "
              f"+{stats['checkpoints']:>4} checkpoints, "
              f"{stats['graded']:>3} fully graded")
    if not args.dry_run:
        print(f"\n{total_checkpoints} checkpoints written across {len(results)} agents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
