"""Tests for the shared markdown link parser (nexus.markdown_links)."""

from __future__ import annotations

from nexus.markdown_links import extract_md_links, resolve_targets, strip_code_fences


def extract(body: str) -> set[str]:
    return extract_md_links(body)


class TestExtraction:
    def test_markdown_link(self):
        assert extract("see [notes](other.md)") == {"other.md"}

    def test_markdown_link_anchor_stripped(self):
        assert extract("see [a](notes/a.md#section)") == {"notes/a.md"}

    def test_vault_scheme_link(self):
        assert extract("see [a](vault://notes/a.md)") == {"notes/a.md"}

    def test_wiki_link(self):
        assert extract("compare [[Nexus Overview]] and [[notes/a.md]]") == {
            "Nexus Overview",
            "notes/a.md",
        }

    def test_wiki_link_alias(self):
        assert extract("[[Nexus|the platform]]") == {"Nexus"}

    def test_wiki_link_anchor_and_alias(self):
        assert extract("[[notes/a.md#Setup|setup docs]]") == {"notes/a.md"}

    def test_bare_path_mention(self):
        assert extract("copy it from docs/notes/a.md please") == {"docs/notes/a.md"}

    def test_single_segment_bare_mention_ignored(self):
        # "a.md" alone is too FP-prone to count as a link
        assert extract("the file a.md is fine") == set()

    def test_links_inside_code_fences_ignored(self):
        body = "text [ok](good.md)\n```sh\ncat docs/evil.md\n[[wiki]]\n```\nmore"
        assert extract(body) == {"good.md"}

    def test_tilde_fences_ignored(self):
        assert extract("~~~\n[a](x.md)\n~~~") == set()

    def test_dedup_across_forms(self):
        assert extract("[a](a.md) and [[a.md]] and bare a/b/a.md refs") == {"a.md", "a/b/a.md"}


class TestResolution:
    PATHS = {"a.md", "notes/a.md", "notes/deep/b.md", "Nexus Overview.md", "dup/x.md", "dup/y.md"}

    def test_exact_match(self):
        out = resolve_targets({"a.md"}, src_rel="a.md", path_set=self.PATHS)
        assert out == set()

    def test_exact_match_other(self):
        out = resolve_targets({"notes/a.md"}, src_rel="a.md", path_set=self.PATHS)
        assert out == {"notes/a.md"}

    def test_leading_slash_normalized(self):
        out = resolve_targets({"/notes/a.md"}, src_rel="a.md", path_set=self.PATHS)
        assert out == {"notes/a.md"}

    def test_extensionless_appends_md(self):
        out = resolve_targets({"notes/a"}, src_rel="a.md", path_set=self.PATHS)
        assert out == {"notes/a.md"}

    def test_wiki_title_stem_match(self):
        out = resolve_targets({"Nexus Overview"}, src_rel="a.md", path_set=self.PATHS)
        assert out == {"Nexus Overview.md"}

    def test_relative_to_source_folder(self):
        out = resolve_targets({"deep/b.md"}, src_rel="notes/a.md", path_set=self.PATHS)
        assert out == {"notes/deep/b.md"}

    def test_ambiguous_stem_resolves_deterministically(self):
        out = resolve_targets({"dup/x"}, src_rel="a.md", path_set=self.PATHS)
        assert out == {"dup/x.md"}

    def test_self_link_excluded(self):
        out = resolve_targets({"a.md", "notes/a.md"}, src_rel="a.md", path_set=self.PATHS)
        assert "a.md" not in out

    def test_unresolvable_dropped(self):
        out = resolve_targets({"missing.md"}, src_rel="a.md", path_set=self.PATHS)
        assert out == set()


def test_strip_code_fences_keeps_prose():
    assert strip_code_fences("a\n```\ncode\n```\nb") == "a\n\nb"
