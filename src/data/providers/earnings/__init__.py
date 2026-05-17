"""Earnings-domain providers sub-package.

Each module in this package registers a provider for the ``earnings``
domain via ``@register`` from ``data.registry``.  Import the module to
activate its registration; ``data/__init__.py`` drives the bulk import
on package load.
"""
