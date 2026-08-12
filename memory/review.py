"""Cross-agent performance review.

Reads every agent's decision log and reports alpha by agent, by feature, and by
sizing/horizon bucket.

The report exists as much to prevent a wrong conclusion as to support a right
one. Per-decision alpha has a standard deviation of roughly 6 percentage
points, so a dozen decisions cannot separate skill from luck — an agent ranking
built on them looks authoritative and means nothing. Every per-agent figure is
therefore printed with its sample size, and rankings below SAMPLE_THRESHOLD are
labelled as insufficient rather than silently shown.
"""

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from memory.memory import best_checkpoint

# Below this many graded decisions, an agent's mean alpha is not interpretable.
# Derived from sd ~6.1pp per decision: detecting a 2pp edge at 95% confidence
# needs (1.96 * 6.1 / 2)^2 ~ 36 samples.
SAMPLE_THRESHOLD = 30
ASSUMED_SD = 6.1


def agent_dirs(root: Path) -> list:
    """Every agent memory directory under the shared root."""
    if not root.exists():
        return []
    return sorted(d for d in root.iterdir()
                  if d.is_dir() and (d / "memory" / "decisions.jsonl").exists())


def load_decisions(root: Path) -> list:
    """All decisions across all agents, tagged with the agent they came from."""
    records = []
    for directory in agent_dirs(root):
        path = directory / "memory" / "decisions.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec["agent"] = directory.name
            records.append(rec)
    return records


def alpha_at(rec: dict, checkpoint: str):
    """Alpha at a named checkpoint, or None if that window hasn't landed."""
    data = (rec.get("checkpoints") or {}).get(checkpoint)
    return data.get("alpha_pct") if data else None


def summarize(values: list) -> dict:
    """Mean, win rate and a 95% confidence interval for a set of alphas."""
    n = len(values)
    if not n:
        return {"n": 0, "mean": None, "win_pct": None, "ci": None}
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if n > 1 else ASSUMED_SD
    return {
        "n": n,
        "mean": mean,
        "win_pct": 100 * sum(1 for v in values if v > 0) / n,
        "ci": 1.96 * sd / math.sqrt(n),
    }


def by_agent(records: list, checkpoint: str) -> dict:
    groups = defaultdict(list)
    for rec in records:
        alpha = alpha_at(rec, checkpoint)
        if alpha is not None:
            groups[rec.get("agent")].append(alpha)
    return {agent: summarize(values) for agent, values in groups.items()}


def by_feature(records: list, checkpoint: str, feature: str, buckets) -> dict:
    """Pooled alpha per bucket of one feature.

    Pooled across all agents on purpose: this is where the sample sizes are
    large enough to mean something.
    """
    groups = defaultdict(list)
    for rec in records:
        alpha = alpha_at(rec, checkpoint)
        value = (rec.get("features") or {}).get(feature)
        if alpha is None or value is None:
            continue
        groups[_bucket(value, buckets)].append(alpha)
    return {label: summarize(values) for label, values in groups.items()}


def _bucket(value, buckets):
    if isinstance(value, str) or isinstance(value, bool):
        return str(value)
    for threshold, label in buckets:
        if value < threshold:
            return label
    return buckets[-1][1] if buckets else str(value)


def _fmt(stats: dict, enforce_min: bool) -> str:
    if not stats["n"]:
        return f"{'-':>9}{'-':>8}{'-':>9}   no data"
    line = (f"{stats['mean']:>+9.2f}{stats['win_pct']:>7.0f}%"
            f"{stats['n']:>9}   ±{stats['ci']:.2f}pp")
    if enforce_min and stats["n"] < SAMPLE_THRESHOLD:
        line += f"   insufficient (need ~{SAMPLE_THRESHOLD})"
    return line


def render(records: list, checkpoint: str = "20d") -> str:
    graded = [r for r in records if alpha_at(r, checkpoint) is not None]
    out = [
        f"StockBeat agent review — checkpoint: {checkpoint}",
        f"{len(records)} decisions logged, {len(graded)} graded at this checkpoint",
        "",
    ]

    buys = [r for r in graded if (r.get("action") or "").upper() == "BUY"]
    sells = [r for r in graded if (r.get("action") or "").upper() != "BUY"]
    out.append("POOLED (all agents)")
    out.append(f"{'group':22}{'alpha':>9}{'win':>8}{'n':>9}")
    for label, rows in (("BUY", buys), ("SELL/EXIT", sells)):
        out.append(f"{label:22}{_fmt(summarize([alpha_at(r, checkpoint) for r in rows]), False)}")
    out.append("")

    out.append("BY AGENT (BUYs only)")
    out.append(f"{'agent':22}{'alpha':>9}{'win':>8}{'n':>9}")
    ranked = sorted(by_agent(buys, checkpoint).items(),
                    key=lambda kv: kv[1]["mean"], reverse=True)
    for agent, stats in ranked:
        out.append(f"{agent:22}{_fmt(stats, True)}")
    if ranked and all(s["n"] < SAMPLE_THRESHOLD for _, s in ranked):
        out.append("")
        out.append(f"  NOTE: every agent is below {SAMPLE_THRESHOLD} graded decisions. This"
                   " ordering is not")
        out.append("  distinguishable from chance — do not act on it.")
    out.append("")

    feature_buckets = [
        ("position_pct", [(9, "<9%"), (11, "9-11%"), (13, "11-13%"), (99, ">=13%")]),
        ("rsi", [(30, "<30"), (50, "30-50"), (70, "50-70"), (200, ">=70")]),
        ("score", [(0, "<0"), (3, "0-3"), (6, "3-6"), (999, ">=6")]),
        ("sector", []),
    ]
    for feature, buckets in feature_buckets:
        rows = by_feature(buys, checkpoint, feature, buckets)
        if not rows:
            continue
        out.append(f"BY {feature.upper()} (pooled BUYs)")
        out.append(f"{'bucket':22}{'alpha':>9}{'win':>8}{'n':>9}")
        for label, stats in sorted(rows.items(), key=lambda kv: kv[1]["mean"], reverse=True):
            out.append(f"{str(label):22}{_fmt(stats, False)}")
        out.append("")

    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Cross-agent performance review")
    parser.add_argument("--checkpoint", default="20d",
                        choices=["5d", "20d", "60d", "horizon"])
    parser.add_argument("--root", default=str(Path.home() / ".stockbeat-agent"))
    args = parser.parse_args(argv)

    records = load_decisions(Path(args.root))
    if not records:
        print(f"No decision logs found under {args.root}")
        return 1
    print(render(records, args.checkpoint))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
