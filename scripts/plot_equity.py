"""Render bot-vs-SPY equity curve to a PNG.

Usage:
    PYTHONPATH=src python -m scripts.plot_equity --out docs/performance/2026-05-07.png
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

from baselines.equity_curve import compute_equity_curve


def render(*, db_url: str, out_path: Path) -> None:
    curve = compute_equity_curve(db_url)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not curve.timestamps:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No portfolio_snapshots yet — initialise the bot.",
                ha="center", va="center", fontsize=12)
        ax.set_axis_off()
        fig.savefig(out_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    bot_y = [p * 100 for p in curve.bot_pct]
    spy_y = [p * 100 for p in curve.spy_pct]
    ax.plot(curve.timestamps, bot_y, label="Bot", color="#1f77b4", linewidth=2)
    ax.plot(curve.timestamps, spy_y, label="SPY (buy-and-hold)", color="#888", linewidth=1.5, linestyle="--")
    ax.axhline(0.0, color="#ccc", linewidth=0.8)
    ax.set_ylabel("Return (%)")
    ax.set_xlabel("Time")
    ax.legend(loc="upper left")

    ax2 = ax.twinx()
    excess_y = [p * 100 for p in curve.excess_pct]
    ax2.plot(curve.timestamps, excess_y, color="#2ca02c", linewidth=1.0, alpha=0.6, label="Excess")
    ax2.set_ylabel("Excess (%)")
    ax2.legend(loc="upper right")

    bot_final = bot_y[-1]
    spy_final = spy_y[-1]
    excess_final = excess_y[-1]
    ax.set_title(
        f"Bot {bot_final:+.2f}%   SPY {spy_final:+.2f}%   Excess {excess_final:+.2f}%   "
        f"(anchor: {curve.anchor_tick_id})"
    )

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _resolve_default_db_url() -> str:
    env = os.environ.get("STOCKBOT_ENV", "dev").lower()
    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("STOCKBOT_ENV=prod requires DATABASE_URL")
        return url
    return "sqlite:///data/stockbot.db"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-url", default=None)
    p.add_argument("--out", default="docs/performance/equity.png")
    args = p.parse_args()
    db_url = args.db_url or _resolve_default_db_url()
    out = Path(args.out)
    render(db_url=db_url, out_path=out)
    curve = compute_equity_curve(db_url)
    if curve.timestamps:
        print(f"✓ {len(curve.timestamps)} ticks since reset (anchor: {curve.anchor_tick_id})")
        print(f"✓ Bot: {curve.bot_pct[-1]*100:+.2f}%   "
              f"SPY: {curve.spy_pct[-1]*100:+.2f}%   "
              f"Excess: {curve.excess_pct[-1]*100:+.2f}%")
    print(f"✓ Wrote {out}")


if __name__ == "__main__":
    main()
