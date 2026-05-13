"""SQLAlchemy metadata introspection — composite ``(analyst, ticker, recorded_at)`` index.

Phase 5 (Task 12) adds a single composite index to ``analyst_evidence`` to
support the dominant evidence-retrieval access pattern: filter by analyst +
ticker, ordered by ``recorded_at``. The test asserts the index is declared on
the SQLAlchemy table metadata — it does not require a live engine, so it runs
cheaply alongside the rest of the unit suite.
"""

from __future__ import annotations


def test_analyst_evidence_has_composite_lookup_index() -> None:
    """The Phase-5 composite lookup index is declared on AnalystEvidenceRow.

    Verifies both the index name (``ix_analyst_evidence_lookup``) and the exact
    column order (``analyst, ticker, recorded_at``). Order matters — a different
    ordering would not serve the spec's intended query shape.
    """

    from orchestrator.persistence import AnalystEvidenceRow

    # All indexes declared on the table — names only, for the existence check.
    declared = {ix.name for ix in AnalystEvidenceRow.__table__.indexes}
    assert "ix_analyst_evidence_lookup" in declared, (
        f"composite lookup index missing; got: {sorted(declared)}"
    )

    # Pull the named index back and assert column composition + order.
    target = next(
        ix for ix in AnalystEvidenceRow.__table__.indexes
        if ix.name == "ix_analyst_evidence_lookup"
    )
    cols = [c.name for c in target.columns]
    assert cols == ["analyst", "ticker", "recorded_at"], (
        f"composite index columns out of order; got: {cols}"
    )
