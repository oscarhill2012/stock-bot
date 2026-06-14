"""Strategist v2 — LlmAgent + sequenced StrategistEnricher.

The strategist branch is now a ``SequentialAgent`` of three sub-agents:

1. :class:`StrategistContextShim` — hydrates ``temp:held_positions_view``,
   ``temp:ticker_evidence``, and ``temp:ticker_evidence_objects`` via a
   yielded ``Event(state_delta=…)`` (contract Rule 1).
2. The wrapped ``LlmAgent`` (inside :class:`RetryingAgentWrapper`) — emits
   the *narrow* :class:`StrategistLLMDecision` shape via
   ``output_key="strategist_decision"``.
3. :class:`StrategistEnricher` — reads the narrow LLM output from state,
   runs validation + ``derive_decision_fields``, and yields a single
   ``Event`` whose ``state_delta`` overwrites ``state["strategist_decision"]``
   with the full enriched :class:`StrategistDecision` dump that downstream
   agents (RiskGate, Executor, persistence) consume.

Why the enricher is a BaseAgent rather than an ``after_agent_callback``
----------------------------------------------------------------------
Pre-2026-05-25 the enrichment lived in the LlmAgent's
``after_agent_callback``.  That coupling broke under
:class:`RetryingAgentWrapper`-driven schema-retry: the wrapper buffers
events from the inner LlmAgent across attempts, and on the successful
attempt the ``after_agent_callback`` did not re-fire to enrich the
output.  RiskGate then saw the narrow shape, read
``decision.target_weights`` as the schema default ``{}``, produced zero
orders, and the executor's after-callback asserted ``open without fill
price`` for every open stance on the tick.  See the docstring on
:class:`StrategistEnricher` for the full incident analysis.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── Agent factory ─────────────────────────────────────────────────────────────


def build_strategist():
    """Construct the production Strategist branch — SequentialAgent of
    ``[StrategistContextShim, RetryingAgentWrapper[LlmAgent], StrategistEnricher]``.

    This factory is the **single construction path** for the strategist.  Both
    the live pipeline (``orchestrator.pipeline._build_strategist``) and any
    test that needs a real strategist agent should call this function — there
    is no module-level singleton.  Pre-2026-05-21 the strategist had two
    construction sites: an inline one in ``pipeline.py`` (which production
    used) and a module-level singleton here (which a few tests used) — each
    carried its own ``"gemini-…"`` literal, and a model swap on one silently
    no-op'd on the other.  Centralising via ``config/models.json`` plus this
    factory closes that footgun.

    The branch shape:

    - ``StrategistContextShim`` runs first and hydrates
      ``temp:held_positions_view``, ``temp:ticker_evidence``, and
      ``temp:ticker_evidence_objects`` via a yielded
      ``Event(state_delta=…)`` (contract Rule 1).
    - The wrapped ``LlmAgent`` resolves those keys via ADK's
      instruction-variable substitution and emits its narrow
      :class:`StrategistLLMDecision` via ``output_key="strategist_decision"``.
    - :class:`StrategistEnricher` reads the narrow LLM output from state,
      runs validation + ``derive_decision_fields``, and yields a single
      ``Event(state_delta=…)`` that overwrites ``strategist_decision``
      with the full enriched dump.

    Why the enricher is sequenced as a BaseAgent rather than an
    ``after_agent_callback``
    -----------------------------------------------------------
    See :class:`agents.strategist.enricher.StrategistEnricher` for the full
    incident analysis.  Summary: ``after_agent_callback`` is lifecycle-coupled
    to the LlmAgent call and silently misfires under
    :class:`RetryingAgentWrapper`-driven schema-retry.  Sequencing the
    enricher as its own BaseAgent makes the enrichment run unconditionally
    once the wrapper has produced a successful LLM response — regardless of
    how many retries were needed inside.

    Why the retry wrap is **inside** the SequentialAgent
    ----------------------------------------------------
    The original implementation wrapped the whole SequentialAgent in a
    ``RetryingAgentWrapper`` at the pipeline-composition layer.  That broke
    the strategist with ``KeyError: 'Context variable not found:
    temp:held_positions_view'`` because the retry wrapper buffers every
    event the inner yields, then forwards them only on success.  When the
    inner is a SequentialAgent, ContextShim's ``state_delta`` event is
    buffered — the ADK Runner never sees it, never applies it to
    ``ctx.session.state``, and the LlmAgent's ``inject_session_state`` step
    fails before any 429 risk even materialises.

    The fix: wrap only the ``LlmAgent`` (the unit that can actually 429).
    ContextShim runs unwrapped, and the enricher runs unwrapped — both yield
    ``state_delta`` events that the outer Runner applies.  See
    :mod:`agents.llm_retry` for the wrap's invariants.

    Returns
    -------
    google.adk.agents.SequentialAgent
        The ``"StrategistBranch"`` SequentialAgent ready to be added to the
        pipeline's top-level SequentialAgent.  ``branch.sub_agents[1]`` is a
        ``RetryingAgentWrapper``; the inner ``LlmAgent`` is at
        ``branch.sub_agents[1].inner`` for tests that need to inspect
        LlmAgent attributes (model, callbacks, output_key, etc.).
        ``branch.sub_agents[2]`` is the :class:`StrategistEnricher`.
    """

    import os

    from google.adk.agents import LlmAgent, SequentialAgent
    from google.genai import types as genai_types

    from agents.analysts._common import _chain_after, _chain_before
    from agents.llm_retry import RetryingAgentWrapper, build_retry_policies
    from agents.strategist.context_shim import StrategistContextShim
    from agents.strategist.enricher import StrategistEnricher
    from agents.strategist.prompts import STRATEGIST_INSTRUCTION
    from agents.strategist.schema import StrategistLLMDecision
    from config.models import get_models_config
    from config.strategist import get_strategist_config
    from observability.terminal_log import make_observability_callbacks
    from observability.trace import make_llm_trace_callbacks

    # Read the model ID from the central config.  One JSON edit moves both
    # live and backtest runs — no shadow constant to forget.
    model_name = get_models_config().strategist

    # Read the per-call runtime caps from config/strategist.json.  These drive
    # the LlmAgent's token budget and the RetryingAgentWrapper's timeout +
    # retry budgets — the single source of truth so tuning one JSON key takes
    # effect everywhere.
    llm_caps = get_strategist_config().llm

    # Observability callbacks — emit one terminal-log row for the strategist
    # LLM call.  Only wired when STOCKBOT_TERMINAL_LOG=1 so backtest replays
    # and unit tests add zero overhead.  The strategist has no per-ticker
    # progress counter (it makes one call per tick), so ticker_index=1 /
    # ticker_count=1 is used to suppress the N/M column meaningfully.
    obs_before = None
    obs_after  = None

    if os.environ.get("STOCKBOT_TERMINAL_LOG") == "1":
        obs_before, obs_after = make_observability_callbacks(
            analyst      = "strategist",
            ticker       = "decision",
            ticker_index = 1,
            ticker_count = 1,
            model_name   = model_name,
        )

    # Trace callbacks are opt-in via STOCKBOT_TRACE=1.  Zero-cost when off:
    # both callbacks remain ``None`` and ADK skips the dispatch entirely.
    trace_before = None
    trace_after  = None

    if os.environ.get("STOCKBOT_TRACE") == "1":
        trace_before, trace_after = make_llm_trace_callbacks(
            "05_strategist_llm",
            model=model_name,
        )

    # ── Local probe (env-gated, off by default) ──────────────────────────────
    # When STRATEGIST_PROBE_DIR is set, dump rendered prompt, raw response,
    # and usage_metadata to that directory.  Diagnostic for tracking down
    # output truncation / verbose-Gemini-budget issues.  Zero-cost when off.
    probe_before = None
    probe_after  = None

    probe_dir = os.environ.get("STRATEGIST_PROBE_DIR")
    if probe_dir:
        from pathlib import Path

        from observability.trace import _extract_content_text  # reuse the existing text walker

        _probe_path = Path(probe_dir)
        _probe_path.mkdir(parents=True, exist_ok=True)
        _counter = {"n": 0}                                                     # mutable closure state — one prompt/response pair per call

        def _stringify(obj):                                                    # noqa: ANN001, ANN202 — helper closure
            """Coerce a system_instruction-or-content value to its text.

            Vertex's ``system_instruction`` may arrive as ``str``, ``Content``
            (with ``.parts``), or ``Part`` directly.  The trace helper only
            handles the ``Content`` case, so we layer string handling on top.
            """
            if obj is None:
                return ""
            if isinstance(obj, str):
                return obj
            walked = _extract_content_text(obj)
            if walked:
                return walked
            return repr(obj)                                                    # last resort — surfaces unknown shapes for debugging

        def probe_before(callback_context, llm_request):                        # noqa: ANN001 — ADK callback signatures
            """Dump the full rendered prompt (system + user) plus generation config."""
            _counter["n"] += 1
            idx = _counter["n"]

            cfg          = getattr(llm_request, "config", None)
            system_text  = _stringify(getattr(cfg, "system_instruction", None))

            # Walk contents one-by-one so we can label role + index.
            chunks: list[str] = []
            for i, content in enumerate(getattr(llm_request, "contents", None) or []):
                role = getattr(content, "role", "?")
                text = _stringify(content)
                chunks.append(f"## content[{i}] role={role} ({len(text)} chars)\n{text}")
            user_block = "\n\n".join(chunks) if chunks else "(no contents)"

            # Surface every config field that could plausibly influence
            # decoding (temperature / top_p / top_k / thinking budget / etc).
            cfg_dump = "(no config)"
            if cfg is not None:
                try:
                    cfg_dump = cfg.model_dump_json(indent=2, exclude_none=True)
                except Exception:                                               # noqa: BLE001 — best-effort diagnostic
                    cfg_dump = repr(cfg)

            payload = (
                f"# model: {getattr(llm_request, 'model', '?')}\n"
                f"# config:\n{cfg_dump}\n\n"
                f"# system_instruction ({len(system_text)} chars)\n{system_text}\n\n"
                f"# contents:\n{user_block}\n"
            )
            (_probe_path / f"call_{idx:02d}_prompt.txt").write_text(payload)
            return None

        def probe_after(callback_context, llm_response):                        # noqa: ANN001 — ADK callback signatures
            """Dump raw response text, usage_metadata, and finish_reason."""
            idx       = _counter["n"]
            resp_text = _extract_content_text(getattr(llm_response, "content", None))
            usage     = getattr(llm_response, "usage_metadata", None)
            finish    = getattr(llm_response, "finish_reason", None)
            usage_str = repr(usage) if usage else "(no usage_metadata)"
            payload   = (
                f"# finish_reason: {finish}\n"
                f"# usage_metadata: {usage_str}\n"
                f"# response ({len(resp_text)} chars)\n"
                f"{resp_text}\n"
            )
            (_probe_path / f"call_{idx:02d}_response.txt").write_text(payload)
            return None

    # Chain observability + trace + probe callbacks.
    before_model = _chain_before(obs_before, trace_before, probe_before)
    after_model  = _chain_after(obs_after, trace_after, probe_after)

    # The inner LlmAgent — emits the *narrow* StrategistLLMDecision shape
    # via output_key.  No ``after_agent_callback`` here: enrichment lives in
    # the sequenced :class:`StrategistEnricher` so it survives schema-retry
    # inside :class:`RetryingAgentWrapper` (see module docstring + the
    # enricher class docstring for the incident this design fixes).
    llm = LlmAgent(
        name                    = "Strategist",
        model                   = model_name,
        instruction             = STRATEGIST_INSTRUCTION,
        output_schema           = StrategistLLMDecision,
        output_key              = "strategist_decision",
        # Suppress ADK's default behaviour of forwarding every upstream
        # agent's content events into this agent's prompt as conversation
        # history.  Without this, analyst sub-agent outputs arrive as
        # "[NewsAnalyst_X] said: {json}" context lines — duplicating data
        # the curated ``## Ticker Evidence`` section already renders.
        # The strategist runs purely on its instruction template plus the
        # ``{temp:*}`` placeholders hydrated by StrategistContextShim.
        #
        # Schema-retry safety: the RetryingAgentWrapper feeds correction
        # feedback via the ``{temp:_last_schema_error}`` instruction
        # placeholder (seeded in StrategistContextShim, overwritten by the
        # wrapper before each retry).  It does NOT rely on conversation
        # history — so ``include_contents='none'`` is safe.
        include_contents        = "none",
        before_model_callback   = before_model,
        after_model_callback    = after_model,
        generate_content_config = genai_types.GenerateContentConfig(
            max_output_tokens  = llm_caps.max_output_tokens,
            temperature        = 0.3,                                           # probe: lower temp to discourage rambling / attractor states
            frequency_penalty  = 0.5,                                           # probe: penalise verbatim token-level repetition
            presence_penalty   = 0.5,                                           # probe: penalise re-using already-emitted tokens
            thinking_config    = genai_types.ThinkingConfig(thinking_budget=128), # probe: minimum thinking budget allowed on 2.5-pro
        ),
    )

    # Wrap the LlmAgent in the retry layer so transient Vertex 429s trigger
    # exponential backoff, wall-clock timeouts abort hung calls, and schema
    # failures trigger re-prompts.  The wrap goes here (inside the
    # SequentialAgent), not around the SequentialAgent itself — see the
    # docstring for why.  The retry budgets (timeout_retries, schema_retries)
    # come from the same strategist.llm config section as the token budget.
    wrapped_llm = RetryingAgentWrapper(
        name                   = "StrategistLlmRetrying",
        inner                  = llm,
        timeout_seconds        = llm_caps.timeout_seconds,
        policies               = build_retry_policies(
            timeout_retries = llm_caps.timeout_retries,
            schema_retries  = llm_caps.schema_retries,
        ),
        retry_state_key        = "temp:_obs_strategist_retries",
        # Pipe Pydantic ValidationError text back to the LLM via the
        # ``{temp:_last_schema_error}`` placeholder in the strategist
        # instruction.  The empty default is seeded by
        # :class:`StrategistContextShim` so the first attempt's template
        # substitution still resolves; the wrapper overwrites this slot
        # before each schema retry so the model sees what it got wrong.
        schema_error_state_key = "temp:_last_schema_error",
    )

    return SequentialAgent(
        name       = "StrategistBranch",
        sub_agents = [
            StrategistContextShim(),
            wrapped_llm,
            StrategistEnricher(),
        ],
    )
