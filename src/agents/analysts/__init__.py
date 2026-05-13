"""Analyst pool — ParallelAgent wrapping all five analysts (Phase 5).

Technical, Fundamental, News, and SmartMoney are ``LlmAgent`` instances
(Technical and SmartMoney are pending conversion to ``BaseAgent`` in Tasks 8
and 9 respectively).  Social (added in Task 7) is already a ``BaseAgent``
subclass: its ``before_agent_callback`` fetches raw data, ``_run_async_impl``
derives verdicts deterministically via heuristics, and the
``after_agent_callback`` converts verdicts to ``AnalystEvidence`` records.
"""
from google.adk.agents import ParallelAgent

from .fundamental.agent import fundamental_analyst
from .news.agent import news_analyst
from .smart_money.agent import smart_money_analyst
from .social.agent import social_analyst
from .technical.agent import technical_analyst

analyst_pool = ParallelAgent(
    name="AnalystPool",
    sub_agents=[
        technical_analyst,
        fundamental_analyst,
        news_analyst,
        social_analyst,
        smart_money_analyst,
    ],
)

__all__ = ["analyst_pool"]
