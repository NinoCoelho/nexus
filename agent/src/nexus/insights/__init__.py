"""Session analytics engine for Nexus.

Adapted from Hermes' ``agent/insights.py`` for Nexus's slimmer session
schema. Unlike Hermes — which tracks input/output/cache tokens, cost,
source platform, and billing metadata per session — Nexus currently
stores only ``(id, title, context, created_at, updated_at)`` on
``sessions`` and ``(role, content, tool_calls, tool_call_id, created_at)``
on ``messages``. We aggregate what's there:

* Session counts + activity by day-of-week and hour
* Messages per role (user / assistant / tool)
* Tool usage — extracted from the ``tool_calls`` JSON on assistant
  messages (Nexus stores its own shape, ``[{id, name, arguments}]``,
  not OpenAI's ``{function: {name}}``; see ``_extract_tool_name``)
* Top sessions by message count + by tool-call count
* Activity streaks

Token/cost breakdowns are intentionally omitted until the session
schema learns to capture ``usage`` from provider responses. When that
lands, pricing can be slotted in via the ``cost`` hooks left empty
in :meth:`InsightsEngine.generate`.
"""

from .engine import InsightsEngine
from .terminal import format_terminal

__all__ = ["InsightsEngine", "format_terminal"]
