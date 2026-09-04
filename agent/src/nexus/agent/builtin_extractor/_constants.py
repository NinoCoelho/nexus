"""Static lookup tables and regex constants for the builtin entity extractor."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# spaCy NER label → ontology entity type (direct, no embedding needed)
#
# Only mappings with near-perfect precision are direct. ORG/PRODUCT/GPE/LOC/
# FAC intentionally have no direct mapping — "ORG → project" turned every
# company into a project and "GPE → resource" turned every city into a
# document. Those labels go through the embedding classifier with a label
# hint (see SPACY_LABEL_HINTS) so the ontology type reflects the entity,
# not the label bucket.
# ---------------------------------------------------------------------------

SPACY_LABEL_MAP: dict[str, str] = {
    "PERSON": "person",
    "PER": "person",
}

# Human-readable label descriptions appended to the entity name before type
# classification — gives the embedder real signal instead of a bare name.
SPACY_LABEL_HINTS: dict[str, str] = {
    "PERSON": "a person",
    "PER": "a person",
    "ORG": "an organization, company, agency or institution",
    "PRODUCT": "a product, piece of software or technology",
    "GPE": "a country, city or state",
    "LOC": "a geographic location or region",
    "FAC": "a building, facility or structure",
    "EVENT": "an event",
    "WORK_OF_ART": "a creative work such as a book, film or title",
    "LAW": "a law or legal document",
    "NORP": "a nationality, religious or political group",
    "LANGUAGE": "a natural language",
}

# spaCy labels that are NOT knowledge-graph entities — skip entirely
_SKIP_LABELS: frozenset[str] = frozenset({
    "CARDINAL", "DATE", "MONEY", "QUANTITY", "ORDINAL", "PERCENT", "TIME",
})

# ---------------------------------------------------------------------------
# Bilingual prototype phrases (en + pt) used as fallback for the multilingual
# embedder. Each phrase concatenates English and Portuguese tokens so a
# single embedding anchors both languages — multilingual-MiniLM keeps them
# in close subspaces, but anchoring with native tokens lifts recall when
# the entity name itself is in pt.
# ---------------------------------------------------------------------------

TYPE_PROTOTYPES: dict[str, list[str]] = {
    "person": ["person individual human being someone | pessoa indivíduo humano alguém"],
    "project": ["project initiative task program undertaking plan | projeto iniciativa programa empreendimento"],
    "concept": ["concept idea theory principle notion abstraction topic | conceito ideia teoria princípio noção tópico"],
    "technology": ["technology tool framework software system platform language library | tecnologia ferramenta framework sistema plataforma linguagem biblioteca"],
    "decision": ["decision choice conclusion judgment determination resolution | decisão escolha conclusão julgamento determinação resolução"],
    "resource": ["resource document material asset reference data source | recurso documento material ativo referência fonte"],
}

RELATION_PROTOTYPES: dict[str, list[str]] = {
    "uses": ["uses utilizes employs leverages applies | usa utiliza emprega aplica"],
    "depends_on": ["depends on requires needs relies on necessitates | depende de requer precisa necessita"],
    "part_of": ["part of component of subset of belongs to contained in member of | parte de componente de pertence a contido em membro de"],
    "created_by": ["created by made by built by developed by authored by designed by | criado por feito por desenvolvido por projetado por"],
    "related_to": ["related to connected to associated with linked to involves | relacionado a conectado a associado com envolve"],
}

# Short / generic noun phrases to skip
_STOP_NOUNS: frozenset[str] = frozenset({
    "this", "that", "these", "those", "it", "they", "we", "you", "he", "she",
    "i", "me", "him", "her", "us", "them", "my", "your", "his", "its",
    "our", "their", "what", "which", "who", "whom", "whose",
    "everyone", "everything", "someone", "something", "anyone", "anything",
    "nothing", "nobody", "none", "all", "some", "many", "few", "much",
    "more", "most", "other", "another", "such", "way", "thing", "things",
    "point", "lot", "bit", "part", "rest", "kind", "sort", "case",
    "matter", "reason", "sense", "idea", "fact", "question", "issue",
    "problem", "place", "time", "day", "today", "week", "month", "year",
    "work", "job", "need", "use", "end", "start", "change", "move",
    "look", "try", "help", "call", "talk", "step", "test", "run",
    "example", "data", "info", "list", "note", "notes", "text", "file",
    "content", "line", "section", "name", "number", "key", "value",
    "set", "group", "type", "form", "field", "result", "results",
    "first", "second", "third", "next", "last", "new", "old",
    "good", "bad", "great", "right", "best", "better", "different",
    "important", "main", "simple", "possible", "real", "true",
})

# Regex: skip noun phrases that are purely numeric / money-like
_NUMERIC_RE = re.compile(r'^[\$\€\£]?[\d.,]+%?$')
