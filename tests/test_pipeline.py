import json

import pytest

from legislink import annotate, resolve_text
from legislink.pipeline import build_index


def test_annotate_markdown_links_resolved_citations():
    result = resolve_text("See section 6 of the Human Rights Act 1998 today.")
    out = annotate(result)
    assert out == (
        "See [section 6 of the Human Rights Act 1998]"
        "(https://www.legislation.gov.uk/ukpga/1998/42/section/6) today."
    )


def test_annotate_preserves_text_outside_citations():
    text = "Nothing to see here."
    assert annotate(resolve_text(text)) == text


def test_annotate_html_escapes_and_carries_metadata():
    result = resolve_text("A & B cited the Human Rights Act 1998.")
    out = annotate(result, fmt="html")
    assert "A &amp; B" in out
    assert 'href="https://www.legislation.gov.uk/ukpga/1998/42"' in out
    assert 'data-method="exact"' in out
    assert 'data-confidence="1.00"' in out


def test_annotate_respects_min_confidence():
    text = "The Companies Act 2006 applies. Section 172 imposes a duty."
    result = resolve_text(text)
    linked = annotate(result, min_confidence=0.0)
    strict = annotate(result, min_confidence=0.9)
    assert linked.count("](") == 2
    assert strict.count("](") == 1  # the 0.70 "context" pinpoint is left alone


def test_annotate_escapes_square_brackets_in_the_surface():
    result = resolve_text("See the Human Rights Act 1998.")
    assert r"\[" not in annotate(result)  # nothing to escape here


def test_annotate_rejects_an_unknown_format():
    with pytest.raises(ValueError):
        annotate(resolve_text("the Human Rights Act 1998"), fmt="latex")


def test_annotated_markdown_round_trips_the_plain_text():
    text = "Section 6 of the Human Rights Act 1998 and Part 3 of the Equality Act 2010."
    out = annotate(resolve_text(text))
    # Stripping the markdown link syntax must give back the original text.
    import re
    assert re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out) == text


def test_document_result_serialises_to_json():
    result = resolve_text("section 6 of the Human Rights Act 1998")
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["stats"]["resolved"] == 1
    assert payload["citations"][0]["provision_label"] == "section 6"
    assert payload["works"][0]["id"] == "ukpga-1998-42"


def test_distinct_works_are_deduplicated():
    result = resolve_text(
        "the Human Rights Act 1998 and section 6 of the Human Rights Act 1998"
    )
    assert len(result.works()) == 1
    assert len(result.citations) == 2


# -- corpus index ----------------------------------------------------------

@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "a.txt").write_text(
        "Section 6 of the Human Rights Act 1998 applies.", encoding="utf-8"
    )
    (tmp_path / "b.txt").write_text(
        "The Human Rights Act 1998, s 3, and the Theft Act 1968 (c. 60).", encoding="utf-8"
    )
    (tmp_path / "ignored.bin").write_bytes(b"\x00\x01")
    return tmp_path


def test_build_index_groups_by_work(corpus):
    index = build_index([corpus])
    assert index["stats"]["documents"] == 2
    hra = next(w for w in index["works"] if w["id"] == "ukpga-1998-42")
    assert hra["mentions"] == 2
    assert len(hra["cited_by"]) == 2
    pinpoints = sorted(p for v in hra["cited_by"].values() for p in v)
    assert pinpoints == ["section 3", "section 6"]


def test_build_index_ranks_by_mention_count(corpus):
    index = build_index([corpus])
    counts = [w["mentions"] for w in index["works"]]
    assert counts == sorted(counts, reverse=True)


def test_build_index_reports_unresolved_references(tmp_path):
    (tmp_path / "c.txt").write_text(
        "The Interplanetary Sandwich Act 1977 applies.", encoding="utf-8"
    )
    index = build_index([tmp_path])
    assert index["unresolved"]
    assert index["stats"]["resolved_citations"] == 0


def test_build_index_skips_unreadable_suffixes(corpus):
    index = build_index([corpus])
    assert all(not d["path"].endswith(".bin") for d in index["documents"])
