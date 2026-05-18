"""Per-ticker smart-money aggregate model.

Phase 7.6 introduces this model to replace the category-first nested
dict (``state["smart_money_data"]["politicians"][ticker]``) with a
ticker-first shape (``state["smart_money_data"][ticker]``).  See spec
``docs/Phase7.5-more-cleanup/specs/data_shape_contracts.md`` §3
for rationale.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Both classes confirmed in data.models.trades by Task 3 audit.
from data.models.trades import NotableHolder, PoliticianTrade


class SmartMoneyRaw(BaseModel):
    """Per-ticker smart-money payload — politician trades and notable-holder
    filings for a single ticker.

    Used as the value type in ``state["smart_money_data"][ticker]`` after
    Phase C of the data-shape contracts rollout (Task 17).

    ``extra="forbid"`` ensures that typos (e.g. ``politicans``) surface as
    ``ValidationError`` at construction time rather than silently being
    dropped, which would produce empty lists in downstream analysis.

    Attributes:
        politicians:      Disclosed trades by elected officials (STOCK Act
                          filings) for this ticker.  Empty list when none
                          are available or the source is disabled.
        notable_holders:  SC 13D/13G beneficial-ownership disclosures for
                          this ticker.  Empty list when none are available.
    """

    model_config = ConfigDict(extra="forbid")

    politicians: list[PoliticianTrade] = Field(
        default_factory=list,
        description="Politician trade disclosures for this ticker.",
    )

    notable_holders: list[NotableHolder] = Field(
        default_factory=list,
        description="SC 13D/G beneficial-ownership disclosures for this ticker.",
    )
