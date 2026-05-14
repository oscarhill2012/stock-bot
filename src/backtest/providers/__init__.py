"""Cache providers package — one module per data domain.

Each provider registers itself with the provider shell as ``upstream="cache"``
so the backtest runner can hot-swap live upstream references with a single
call to ``set_active_provider(domain, "cache")`` per domain.

Importing this package does **not** auto-import the individual providers;
the runner is responsible for importing them (which triggers ``@register``)
before calling ``set_active_provider``.
"""
