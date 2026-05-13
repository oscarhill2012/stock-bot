"""Analyst pool — ParallelAgent wrapping all four analysts."""
from google.adk.agents import ParallelAgent

from .fundamental.agent import fundamental_analyst
from .news.agent import news_analyst
from .smart_money.agent import smart_money_analyst
from .technical.agent import technical_analyst

analyst_pool = ParallelAgent(
    name="AnalystPool",
    sub_agents=[
        technical_analyst,
        fundamental_analyst,
        news_analyst,
        smart_money_analyst,
    ],
)

__all__ = ["analyst_pool"]
