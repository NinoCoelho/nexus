"""Pre-format text so it sounds like a person reading aloud.

Each rule is exercised per language. We don't pin the *exact* num2words
output (their lib changes minor wording across versions); we assert on
key fragments that prove the transformation happened.
"""

from __future__ import annotations

import pytest

from nexus.tts.normalize import normalize_for_speech


# ── Language-agnostic ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "inp,expected_substr,expected_missing",
    [
        ("Olá 🚀 mundo", "Olá  mundo", "🚀"),
        ("Done ✅ now", "Done  now", "✅"),
        ("Status: 📊 📈 done", "Status:    done", "📊"),
        ("flags 🇧🇷 🇺🇸", "flags  ", "🇧🇷"),
    ],
)
def test_emojis_stripped(inp: str, expected_substr: str, expected_missing: str) -> None:
    out = normalize_for_speech(inp)
    # Whitespace collapsing kicks in, so don't assert on exact double-space
    # — just assert the original phrase parts survive and the emoji is gone.
    for word in expected_substr.split():
        if word.strip():
            assert word in out
    assert expected_missing not in out


def test_url_replaced_with_domain() -> None:
    out = normalize_for_speech("Visit https://example.com/foo for more")
    assert "example.com" in out
    assert "https://" not in out
    assert "/foo" not in out


def test_url_without_path() -> None:
    out = normalize_for_speech("Go to https://news.bbc.co.uk", lang="en")
    assert "news.bbc.co.uk" in out


# ── Hashes / UUIDs ─────────────────────────────────────────────────────────


def test_session_id_hash_replaced_en() -> None:
    out = normalize_for_speech(
        "session_id: 251b95a60cb141c092c0025063743909", lang="en",
    )
    assert "identifier" in out
    assert "251b95a60cb141c092c0025063743909" not in out
    # The label survives (underscores stripped for speech) so the
    # listener still has context.
    assert "session id" in out


def test_session_id_hash_replaced_pt() -> None:
    out = normalize_for_speech(
        "session_id: 251b95a60cb141c092c0025063743909", lang="pt",
    )
    assert "identificador" in out
    assert "251b95a60cb141c092c0025063743909" not in out


def test_uuid_replaced() -> None:
    out = normalize_for_speech(
        "Request 8c7f2e1a-1234-4abc-9def-0123456789ab failed", lang="en",
    )
    assert "identifier" in out
    assert "8c7f2e1a" not in out


def test_short_hex_kept_as_is() -> None:
    """16+ char threshold means a short hex like 'deadbeef' stays."""
    out = normalize_for_speech("Color: deadbeef", lang="en")
    assert "deadbeef" in out


def test_sha256_hash_replaced() -> None:
    sha = "a" * 64
    out = normalize_for_speech(f"hash: {sha}", lang="en")
    assert "identifier" in out
    assert sha not in out


def test_decimal_not_treated_as_hash() -> None:
    """Decimals like 3.14 must NOT trigger the hash regex (they're digits
    only, not 16+ contiguous chars). Sanity check the word boundary."""
    out = normalize_for_speech("Pi is 3.14", lang="en")
    assert "identifier" not in out


# ── Tables ─────────────────────────────────────────────────────────────────


def test_table_replaced_in_english() -> None:
    md = "Here:\n| name | age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |\n"
    out = normalize_for_speech(md, lang="en")
    assert "table follows" in out
    assert "name, age" in out
    assert "2 rows" in out
    # The body data shouldn't leak through verbatim.
    assert "Alice" not in out
    assert "30" not in out


def test_table_replaced_in_portuguese() -> None:
    md = "Veja:\n| nome | idade |\n|---|---|\n| Ana | 30 |\n"
    out = normalize_for_speech(md, lang="pt")
    assert "tabela" in out
    assert "nome, idade" in out
    assert "1 linhas" in out or "uma linha" in out  # tolerate either rendering


# ── Fenced blocks ──────────────────────────────────────────────────────────


def test_mermaid_fenced_announced_en() -> None:
    md = "Diagram:\n```mermaid\ngraph TD; A-->B\n```\nDone"
    out = normalize_for_speech(md, lang="en")
    assert "diagram follows" in out
    assert "graph TD" not in out


def test_mermaid_fenced_announced_pt() -> None:
    md = "Diagrama:\n```mermaid\ngraph TD; A-->B\n```\nFim"
    out = normalize_for_speech(md, lang="pt")
    assert "diagrama" in out
    assert "graph TD" not in out


def test_python_code_fence_announced() -> None:
    md = "Run this:\n```python\nprint('hi')\n```"
    out = normalize_for_speech(md, lang="en")
    assert "python code block follows" in out
    assert "print(" not in out


# ── Numbers ────────────────────────────────────────────────────────────────


def test_small_numbers_kept_as_digits() -> None:
    out = normalize_for_speech("I have 3 apples and 5 oranges", lang="en")
    assert "3 apples" in out
    assert "5 oranges" in out


def test_large_number_expanded_en() -> None:
    out = normalize_for_speech("We have 1250 records", lang="en")
    # num2words: "one thousand, two hundred and fifty" or similar variants
    assert "thousand" in out
    assert "1250" not in out
    assert "1,250" not in out


def test_large_number_expanded_pt() -> None:
    out = normalize_for_speech("Temos 1250 registros", lang="pt")
    assert "mil" in out
    assert "1250" not in out


def test_thousands_separator_expanded_pt() -> None:
    # PT uses '.' as thousand separator; we should treat 1.250 as 1250.
    out = normalize_for_speech("Custo de 1.250 reais", lang="pt")
    assert "mil" in out
    assert "1.250" not in out


def test_decimals_read_per_digit_en() -> None:
    out = normalize_for_speech("Pi is roughly 3.14", lang="en")
    assert "point" in out
    assert "3.14" not in out


def test_decimals_read_per_digit_pt() -> None:
    out = normalize_for_speech("Pi vale 3,14", lang="pt")
    assert "vírgula" in out
    assert "3,14" not in out


# ── Dates ──────────────────────────────────────────────────────────────────


def test_date_dd_mm_pt() -> None:
    out = normalize_for_speech("Reunião dia 12/07", lang="pt")
    assert "julho" in out
    assert "12/07" not in out


def test_date_with_year_pt() -> None:
    out = normalize_for_speech("Vence 12/07/2026", lang="pt")
    assert "julho" in out
    assert "2026" not in out  # year expanded
    assert "12/07/2026" not in out


def test_date_mm_dd_en_default() -> None:
    # English convention: 07/12 is July 12.
    out = normalize_for_speech("Meeting on 07/12", lang="en")
    assert "July" in out
    assert "07/12" not in out


def test_date_disambiguates_when_first_field_gt_12() -> None:
    # 25/07 in English can only be DD/MM (no 25th month).
    out = normalize_for_speech("Meeting on 25/07", lang="en")
    assert "July" in out


# ── Times ──────────────────────────────────────────────────────────────────


def test_time_pt_with_minutes() -> None:
    out = normalize_for_speech("às 14:30", lang="pt")
    assert "14:30" not in out
    # "catorze e trinta" or "quatorze e trinta" — accept either.
    assert "trinta" in out


def test_time_pt_on_the_hour() -> None:
    out = normalize_for_speech("às 09:00", lang="pt")
    assert "09:00" not in out
    assert "horas" in out


def test_time_en_with_pm() -> None:
    out = normalize_for_speech("at 14:30", lang="en")
    assert "14:30" not in out
    assert "PM" in out
    assert "2:30" in out


# ── Pipeline integration ──────────────────────────────────────────────────


def test_combined_pipeline_pt() -> None:
    txt = (
        "Reunião dia 12/07 às 14:30 com 1250 pessoas 🚀.\n\n"
        "Agenda:\n| item | tempo |\n|---|---|\n| abertura | 10 min |\n"
    )
    out = normalize_for_speech(txt, lang="pt")
    assert "julho" in out
    assert "trinta" in out
    assert "mil" in out
    assert "🚀" not in out
    assert "tabela" in out
    assert "12/07" not in out


def test_empty_input() -> None:
    assert normalize_for_speech("") == ""
    assert normalize_for_speech("   ") == ""


def test_unknown_language_only_runs_safe_passes() -> None:
    """Unrecognized lang gets emoji/url/whitespace cleanup but no
    number/date expansion (we don't risk wrong output for unsupported langs)."""
    out = normalize_for_speech("Es 12/07 con 1250 elementos 🚀", lang="fr")
    assert "🚀" not in out
    # Numbers untouched (no fr handler in our table)
    assert "1250" in out
    assert "12/07" in out


# ── Remaining-symbol stripping ──────────────────────────────────────────────


def test_path_slashes_removed() -> None:
    out = normalize_for_speech("Saved to boards/research-pipeline.md", lang="en")
    assert "/" not in out
    assert "boards" in out
    assert "research" in out


def test_date_slashes_preserved_for_unsupported_lang() -> None:
    out = normalize_for_speech("Fecha 12/07", lang="fr")
    assert "12/07" in out


def test_date_slashes_consumed_for_en() -> None:
    out = normalize_for_speech("Meeting on 12/07", lang="en")
    assert "/" not in out
    assert "July" in out or "December" in out


def test_em_dash_replaced() -> None:
    out = normalize_for_speech("Done — all files updated", lang="en")
    assert "—" not in out
    assert "Done" in out


def test_en_dash_replaced() -> None:
    out = normalize_for_speech("Pages 10–20", lang="en")
    assert "–" not in out


def test_bare_hash_removed() -> None:
    out = normalize_for_speech("Target textarea#editor", lang="en")
    assert "#" not in out
    assert "textarea" in out
    assert "editor" in out


def test_at_sign_removed() -> None:
    out = normalize_for_speech("Sent to @user", lang="en")
    assert "@" not in out
    assert "user" in out


def test_underscore_removed() -> None:
    out = normalize_for_speech("Ran skill_article_drafter", lang="en")
    assert "_" not in out
    assert "skill" in out
    assert "drafter" in out
