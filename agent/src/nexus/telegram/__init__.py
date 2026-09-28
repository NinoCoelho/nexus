"""Telegram bot integration — long-polling gateway to Nexus chat sessions.

See CLAUDE.md "Telegram" for the architecture overview. The pieces:
- ``api``: thin Telegram Bot API client (httpx).
- ``poller``: background task owning the getUpdates long-poll loop.
- ``router``: update dispatch — auth, commands, chat turns, streaming replies.
- ``commands``: slash-command handlers (/compact, /new, /chats, ...).
- ``bindings``: (chat_id, thread_id) → project + active session mapping.
- ``hitl``: forwards ask_user/terminal HITL prompts as inline keyboards.
- ``formatting``: assistant markdown → Telegram HTML.
"""
