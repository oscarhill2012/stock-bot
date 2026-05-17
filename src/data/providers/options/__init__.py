"""Options domain providers sub-package.

Each module in this package registers a provider for the ``options`` domain
via ``@register`` from ``data.registry``.  Import the module to activate its
registration; ``data/providers/__init__.py`` drives the bulk import on package
load.

v1 note
-------
The only registered provider for this domain in v1 is the live-only
``yfinance`` shell (``options/yfinance.py``).  It returns an empty dict for
any historical ``as_of`` so backtest replay is never blocked by a missing
options cache entry.  A full PIT-correct implementation is deferred to a
follow-up spec (see backlog).
"""
