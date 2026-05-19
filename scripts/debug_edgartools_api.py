"""Throwaway probe to discover the correct edgartools EntityFacts query shape.

The PIT-pin probe found that ``facts.query().by_concept(X).as_of(D).latest()``
returns ``None`` for every fundamental concept on AAPL — suggesting the API
shape ``pit_composite`` is using is wrong for the installed edgartools
version.  This script prints the EntityFacts and query API surface so we
can pick the correct chain.
"""
from __future__ import annotations

import os
from datetime import date

from dotenv import load_dotenv
from edgar import Company, set_identity


def main() -> None:
    """Print edgartools' EntityFacts / FactsQuery API surface for AAPL."""
    load_dotenv()
    set_identity(os.environ["EDGAR_IDENTITY"])

    c     = Company("AAPL")
    facts = c.get_facts()

    print(f"facts type:                 {type(facts).__name__}")
    print(f"facts.__class__.__module__: {facts.__class__.__module__}")

    print("\nfacts public methods/attrs:")
    for name in sorted(n for n in dir(facts) if not n.startswith("_")):
        print(f"  {name}")

    print("\n--- facts.query() ---")
    q = facts.query()
    print(f"query type: {type(q).__name__}")
    print(f"query public methods/attrs:")
    for name in sorted(n for n in dir(q) if not n.startswith("_")):
        print(f"  {name}")

    print("\n--- Try several query chains for EarningsPerShareBasic ---")

    # 1) The chain pit_composite currently uses.
    print("\n[1] facts.query().by_concept('EarningsPerShareBasic').as_of(today).latest()")
    try:
        r = facts.query().by_concept("EarningsPerShareBasic").as_of(date.today()).latest()
        print(f"    result: {r!r}")
    except Exception as exc:
        print(f"    EXCEPTION: {type(exc).__name__}: {exc}")

    # 2) Without .latest() — see if the query is iterable / has results.
    print("\n[2] facts.query().by_concept('EarningsPerShareBasic').as_of(today) — inspect")
    try:
        q = facts.query().by_concept("EarningsPerShareBasic").as_of(date.today())
        print(f"    query type: {type(q).__name__}")
        print(f"    public attrs:")
        for name in sorted(n for n in dir(q) if not n.startswith("_")):
            print(f"      {name}")

        # Try common iteration patterns.
        if hasattr(q, "to_dataframe"):
            df = q.to_dataframe()
            print(f"    to_dataframe rows: {len(df)}")
            if len(df) > 0:
                print(f"    first row: {df.iloc[0].to_dict()}")
        if hasattr(q, "results"):
            r = q.results()
            summary = f"len={len(r)}" if hasattr(r, "__len__") else repr(r)
            print(f"    results: {summary}")
        if hasattr(q, "all"):
            r = q.all()
            summary = f"len={len(r)}" if hasattr(r, "__len__") else repr(r)
            print(f"    all: {summary}")
    except Exception as exc:
        print(f"    EXCEPTION: {type(exc).__name__}: {exc}")

    # 3) With us-gaap namespace prefix.
    print("\n[3] facts.query().by_concept('us-gaap:EarningsPerShareBasic').as_of(today).latest()")
    try:
        r = facts.query().by_concept("us-gaap:EarningsPerShareBasic").as_of(date.today()).latest()
        print(f"    result: {r!r}")
    except Exception as exc:
        print(f"    EXCEPTION: {type(exc).__name__}: {exc}")

    # 4) No .as_of() — does the query return ANY rows for AAPL's EPS?
    print("\n[4] facts.query().by_concept('EarningsPerShareBasic') — no as_of")
    try:
        q = facts.query().by_concept("EarningsPerShareBasic")
        if hasattr(q, "to_dataframe"):
            df = q.to_dataframe()
            print(f"    to_dataframe rows: {len(df)}")
            if len(df) > 0:
                # Print the columns + last 3 rows.
                print(f"    columns: {list(df.columns)}")
                print(f"    tail(3):")
                print(df.tail(3).to_string(max_cols=10, max_colwidth=20))
    except Exception as exc:
        print(f"    EXCEPTION: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
