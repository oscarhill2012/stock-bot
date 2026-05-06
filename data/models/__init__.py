"""Pydantic models for every data-source provider.

Re-exported flat for convenience: `from data.models import StockStats`.
"""
from .bundle import ProviderError, StockSignalBundle
from .filings import Filing
from .market import OHLCBar, StockStats
from .news import NewsArticle
from .sentiment import SocialSentiment, SocialSentimentSnapshot
from .trades import InsiderTrade, PoliticianTrade, TradeSide

__all__ = [
    "Filing",
    "InsiderTrade",
    "NewsArticle",
    "OHLCBar",
    "PoliticianTrade",
    "ProviderError",
    "SocialSentiment",
    "SocialSentimentSnapshot",
    "StockSignalBundle",
    "StockStats",
    "TradeSide",
]
