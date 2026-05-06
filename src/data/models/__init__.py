"""Pydantic models for every data-source provider.

Re-exported flat for convenience: `from data.models import StockStats`.
"""
from .bundle import ProviderError, StockSignalBundle
from .filings import Filing
from .market import OHLCBar, StockStats
from .news import NewsArticle
from .sentiment import SocialSentiment, SocialSentimentSnapshot
from .trades import InsiderTrade, NotableHolder, PoliticianTrade, TradeSide

__all__ = [
    "Filing",
    "InsiderTrade",
    "NewsArticle",
    "NotableHolder",
    "OHLCBar",
    "PoliticianTrade",
    "ProviderError",
    "SocialSentiment",
    "SocialSentimentSnapshot",
    "StockSignalBundle",
    "StockStats",
    "TradeSide",
]
