"""Web Push delivery — VAPID keys + pywebpush fan-out.

Notifications surface HITL prompts (``user_request`` events) to the OS
even when no Nexus tab is open. The browser registers a service worker
that subscribes to a push endpoint; the backend POSTs encrypted
payloads through the browser's push service (FCM/Mozilla/Apple), which
delivers them to the OS notification center.

Keys live in ``~/.nexus/push.json`` (auto-generated on first use) so
they survive across restarts. Subscriptions live in the sessions DB.
"""

from . import keys, sender

__all__ = ["keys", "sender"]
