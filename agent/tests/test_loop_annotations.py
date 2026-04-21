"""Tests for ``_extract_pending_question`` and ``_annotate_short_reply``
— the two pure helpers that let the agent carry a question across
turns and expand terse user replies.
"""

from __future__ import annotations

from nexus.agent.loop import _annotate_short_reply, _extract_pending_question


class TestExtractPendingQuestion:
    def test_no_question_returns_none(self) -> None:
        assert _extract_pending_question("Done.") is None
        assert _extract_pending_question("") is None

    def test_simple_trailing_question(self) -> None:
        assert _extract_pending_question("Shall I proceed?") == "Shall I proceed?"

    def test_picks_last_question_of_many(self) -> None:
        reply = "Got it. First try A? Or B?"
        q = _extract_pending_question(reply)
        assert q is not None
        assert q.endswith("B?")

    def test_strips_preceding_newline_segment(self) -> None:
        reply = "Summary of changes.\nReady to commit?"
        q = _extract_pending_question(reply)
        assert q == "Ready to commit?"

    def test_ignores_question_mark_only(self) -> None:
        # Degenerate but shouldn't crash or return empty string.
        assert _extract_pending_question("?") is None or _extract_pending_question("?") == "?"


class TestAnnotateShortReply:
    def test_no_pending_question_no_annotation(self) -> None:
        assert _annotate_short_reply("yes", None) is None

    def test_affirmative_expands(self) -> None:
        annotated = _annotate_short_reply("yes", "Delete the file?")
        assert annotated is not None
        assert "affirmative" in annotated
        assert "Delete the file?" in annotated

    def test_negative_expands(self) -> None:
        annotated = _annotate_short_reply("no", "Delete the file?")
        assert annotated is not None
        assert "negative" in annotated
        assert "Delete the file?" in annotated

    def test_case_insensitive_and_trims(self) -> None:
        assert _annotate_short_reply("  YES  ", "Proceed?") is not None
        assert _annotate_short_reply("NoPe", "Proceed?") is not None

    def test_non_terse_reply_passes_through(self) -> None:
        # A real sentence shouldn't get annotated even with a pending question.
        assert _annotate_short_reply("yes but wait on the migration", "Ship it?") is None

    def test_unknown_word_no_annotation(self) -> None:
        assert _annotate_short_reply("maybe", "Ship it?") is None
