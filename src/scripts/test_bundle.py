"""Manual smoke test for the data-layer aggregator.

Runs `get_stock_signal_bundle("AAPL")` end-to-end and prints a summary.
Providers without configured API keys (placeholder Finnhub / Quiver
keys are expected) fail gracefully into `bundle.errors`. yfinance and
EDGAR should populate.

Run: python -m scripts.test_bundle [TICKER]
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time

from data import MIN_DECISION_INTERVAL_SECONDS, get_stock_signal_bundle

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")


async def main(ticker: str) -> int:
    print(f"--- Fetching bundle for {ticker} ---")
    print(f"Package floor: MIN_DECISION_INTERVAL_SECONDS = {MIN_DECISION_INTERVAL_SECONDS:.2f}s")

    t0 = time.monotonic()
    bundle = await get_stock_signal_bundle(
        ticker,
        # keep cheap so the test stays under a minute
        history_period="1mo",
        news_lookback_days=3,
        insider_lookback_days=14,
        politician_lookback_days=30,
        filings_per_form=1,
        include_filing_excerpts=False,  # save EDGAR roundtrips for the smoke test
    )
    elapsed = time.monotonic() - t0
    print(f"--- Bundle ready in {elapsed:.1f}s ---\n")

    print(f"ticker:                          {bundle.ticker}")
    print(f"generated_at:                    {bundle.generated_at.isoformat()}")
    print(f"min_decision_interval_seconds:   {bundle.min_decision_interval_seconds:.2f}")
    print()
    print("== stats ==")
    if bundle.stats:
        s = bundle.stats
        print(f"  long_name:        {s.long_name}")
        print(f"  sector:           {s.sector}")
        print(f"  last_price:       {s.last_price}")
        print(f"  market_cap:       {s.market_cap}")
        print(f"  trailing_pe:      {s.trailing_pe}")
        print(f"  beta:             {s.beta}")
        print(f"  history bars:     {len(s.history)}")
    else:
        print("  (none — see errors)")

    print(f"\n== news ({len(bundle.news)} articles) ==")
    for a in bundle.news[:3]:
        print(f"  {a.published_at.date()} {a.source}: {a.headline[:80]}")

    print("\n== social_sentiment ==")
    if bundle.social_sentiment:
        ss = bundle.social_sentiment
        print(f"  aggregate_score:  {ss.aggregate_score:.3f}")
        for snap in ss.snapshots:
            print(f"    {snap.platform}: mentions={snap.mention_count} score={snap.score:.3f}")
    else:
        print("  (none — see errors)")

    print(f"\n== insider_trades ({len(bundle.insider_trades)}) ==")
    for t in bundle.insider_trades[:5]:
        print(f"  {t.transaction_date} {t.side:5s} {t.shares:>10.0f} sh @ {t.price_per_share or '-'} — {t.insider_name} ({t.insider_title})")

    print(f"\n== politician_trades ({len(bundle.politician_trades)}) ==")
    for t in bundle.politician_trades[:5]:
        print(f"  {t.transaction_date} {t.side:5s} {t.politician} ({t.party})")

    print(f"\n== filings ({len(bundle.filings)}) ==")
    for f in bundle.filings:
        print(f"  {f.filed_at.date()} {f.form_type:6s} {f.accession_no} — {f.title[:60]}")

    print(f"\n== errors ({len(bundle.errors)}) ==")
    for err in bundle.errors:
        print(f"  [{err.provider}] {err.message}")

    return 0 if bundle.stats else 1


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    sys.exit(asyncio.run(main(ticker)))
