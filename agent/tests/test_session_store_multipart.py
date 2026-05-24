"""Persistence round-trip for multipart ``ChatMessage.content``.

Regression test for the production crash where ``replace_history`` rejected
messages whose content was a ``list[ContentPart]`` (image/audio/document
attachments) because the loom translator only handled ``str``. Also
covers the read-back path: SQLite stores multipart as a JSON string
(via loom's ``_serialize_content``) and the nexus reader must reverse
that back into ``ContentPart`` instances rather than leaving the JSON
text in the content field.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.agent.llm import ChatMessage, ContentPart, Role
from nexus.server.session_store import SessionStore


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(db_path=tmp_path / "sessions.db")


def test_replace_history_accepts_multipart_user_message(store: SessionStore) -> None:
    sess = store.create()
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="what is in this image?"),
            ContentPart(
                kind="image",
                vault_path="uploads/cat.png",
                mime_type="image/png",
            ),
        ],
    )
    # Used to raise pydantic ValidationError when the loom translator
    # passed nexus.ContentPart through to lt.ChatMessage(content=...).
    store.replace_history(sess.id, [msg])


def test_multipart_content_round_trips_through_sqlite(store: SessionStore) -> None:
    sess = store.create()
    written = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="describe this"),
            ContentPart(
                kind="image",
                vault_path="uploads/cat.png",
                mime_type="image/png",
            ),
            ContentPart(
                kind="document",
                vault_path="uploads/notes.pdf",
                mime_type="application/pdf",
            ),
        ],
    )
    store.replace_history(sess.id, [written])

    # Force a fresh read through the raw-SQL path used by ``get()``.
    reloaded = store.get(sess.id)
    assert reloaded is not None
    assert len(reloaded.history) == 1
    msg = reloaded.history[0]
    assert isinstance(msg.content, list), (
        "multipart content must round-trip as a list of ContentPart, "
        f"got {type(msg.content).__name__!r}: {msg.content!r}"
    )
    kinds = [p.kind for p in msg.content]
    assert kinds == ["text", "image", "document"]
    paths = [p.vault_path for p in msg.content if p.kind != "text"]
    assert paths == ["uploads/cat.png", "uploads/notes.pdf"]


def test_autotitle_does_not_crash_on_multipart_content(store: SessionStore) -> None:
    """The auto-title path used to call ``msg.content.strip()`` which
    explodes when ``content`` is a list."""
    sess = store.create()
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="here is a screenshot"),
            ContentPart(
                kind="image",
                vault_path="uploads/cat.png",
                mime_type="image/png",
            ),
        ],
    )
    store.replace_history(sess.id, [msg])
    reloaded = store.get(sess.id)
    assert reloaded is not None
    # First text part wins; trailing words trimmed at 40 chars.
    assert reloaded.title == "here is a screenshot"


def test_legacy_string_content_still_round_trips(store: SessionStore) -> None:
    """Single-text messages must keep using the bare-string path."""
    sess = store.create()
    msg = ChatMessage(role=Role.USER, content="hello world")
    store.replace_history(sess.id, [msg])
    reloaded = store.get(sess.id)
    assert reloaded is not None
    assert reloaded.history[0].content == "hello world"
