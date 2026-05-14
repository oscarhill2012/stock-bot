"""Pydantic models for every data-source provider.

Re-exported flat for convenience: ``from data.models import CompanyRatios``.
"""
from .bundle import ProviderError, StockSignalBundle
from .company_ratios import CompanyRatios
from .filings import Filing
from .market import OHLCBar
from .news import NewsArticle
from .price_history import PriceHistory
from .sentiment import SocialSentiment, SocialSentimentSnapshot
from .trades import (
    Form4Bundle,
    InsiderDerivativeTrade,
    InsiderTrade,
    NotableHolder,
    PoliticianTrade,
    TradeSide,
)

__all__ = [
    "CompanyRatios",
    "Filing",
    "Form4Bundle",
    "InsiderDerivativeTrade",
    "InsiderTrade",
    "NewsArticle",
    "NotableHolder",
    "OHLCBar",
    "PoliticianTrade",
    "PriceHistory",
    "ProviderError",
    "SocialSentiment",
    "SocialSentimentSnapshot",
    "StockSignalBundle",
    "TradeSide",
]
