import pytest

from legislink.extract import extract, find_provisions, find_works
from legislink.models import Provision


def works(text):
    return find_works(text)


def provisions(text):
    return find_provisions(text, [(m.start, m.end) for m in find_works(text)])


# -- work titles -----------------------------------------------------------

@pytest.mark.parametrize(
    "text,title,year",
    [
        ("the Human Rights Act 1998 applies", "Human Rights Act", 1998),
        ("Police and Criminal Evidence Act 1984", "Police and Criminal Evidence Act", 1984),
        ("Land Reform (Scotland) Act 2003", "Land Reform (Scotland) Act", 2003),
        ("Copyright, Designs and Patents Act 1988", "Copyright, Designs and Patents Act", 1988),
        ("Health and Safety at Work etc. Act 1974", "Health and Safety at Work etc. Act", 1974),
        ("The Working Time Regulations 1998", "Working Time Regulations", 1998),
        ("Contracts (Rights of Third Parties) Act 1999", "Contracts (Rights of Third Parties) Act", 1999),
        ("Protection from Harassment Act 1997", "Protection from Harassment Act", 1997),
        ("Offences against the Person Act 1861", "Offences against the Person Act", 1861),
    ],
)
def test_finds_work_titles(text, title, year):
    found = works(text)
    assert len(found) == 1
    assert found[0].ref.title == title
    assert found[0].ref.year == year


def test_a_bare_the_still_does_not_extend_a_title():
    # "of the"/"against the" are allowed mid-title, but a bare "the" is not,
    # or the match runs backwards across the sentence.
    found = works("In Smith v Jones the Human Rights Act 1998 was considered.")
    assert [m.ref.title for m in found] == ["Human Rights Act"]


def test_title_does_not_run_backwards_across_a_sentence():
    # "Jones" and "Smith" are capitalised but are not part of the title.
    found = works("In Smith v Jones the Human Rights Act 1998 was considered.")
    assert [m.ref.title for m in found] == ["Human Rights Act"]


def test_chapter_number_is_captured():
    found = works("the Theft Act 1968 (c. 60)")
    assert found[0].ref.number == "60"


def test_devolved_number_is_captured():
    found = works("Land Reform (Scotland) Act 2003 (asp 2)")
    assert found[0].ref.work_type == "asp"
    assert found[0].ref.number == "2"


def test_short_form_definition_is_captured():
    found = works('the Data Protection Act 2018 ("the DPA 2018") applies')
    assert found[0].short_form == "DPA 2018"


def test_chapter_parenthetical_is_not_a_short_form():
    found = works("the Theft Act 1968 (c. 60)")
    assert found[0].short_form is None


# -- provisions ------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("section 6", [Provision("section", "6")]),
        ("s 45", [Provision("section", "45")]),
        ("s.45", [Provision("section", "45")]),
        ("ss 45 and 46", [Provision("section", "45"), Provision("section", "46")]),
        ("sections 45 to 48", [Provision("section", "45", (), "48")]),
        ("ss 170-177", [Provision("section", "170", (), "177")]),
        ("section 45(2)(a)", [Provision("section", "45", ("2", "a"))]),
        ("Schedule 2", [Provision("schedule", "2")]),
        ("Part 3", [Provision("part", "3")]),
        ("regulation 12", [Provision("regulation", "12")]),
        ("article 6", [Provision("article", "6")]),
        ("s 45A", [Provision("section", "45A")]),
    ],
)
def test_provision_forms(text, expected):
    groups = provisions(text)
    assert [p for g in groups for p in g.chain] == expected


def test_paragraph_of_schedule_nests_outermost_first():
    groups = provisions("paragraph 4 of Schedule 2")
    assert len(groups) == 1
    assert groups[0].chain == (Provision("schedule", "2"), Provision("paragraph", "4"))


def test_work_title_year_is_not_read_as_a_provision():
    # "Regulations 1998" must not become "regulation 1998".
    assert provisions("The Working Time Regulations 1998 apply") == []


# -- binding ---------------------------------------------------------------

def test_forward_binding():
    citations, _ = extract("section 6 of the Human Rights Act 1998")
    assert len(citations) == 1
    assert citations[0].mention.ref.title == "Human Rights Act"
    assert citations[0].provisions == (Provision("section", "6"),)


def test_backward_binding():
    citations, _ = extract("Companies Act 2006, s 172")
    assert len(citations) == 1
    assert citations[0].provisions == (Provision("section", "172"),)


def test_backward_binding_survives_a_line_wrap():
    citations, _ = extract("the Public Interest Disclosure Act 1998,\n    ss 1-3")
    assert len(citations) == 1
    assert citations[0].mention.ref.title == "Public Interest Disclosure Act"


def test_backward_binding_does_not_cross_a_paragraph_break():
    citations, _ = extract("the Theft Act 1968\n\n    section 1 is engaged")
    assert len(citations) == 2
    assert citations[1].mention is None


def test_schedule_to_the_act_binds():
    citations, _ = extract("paragraph 4 of Schedule 2 to the Companies Act 2006")
    assert len(citations) == 1
    assert citations[0].mention.ref.title == "Companies Act"


def test_unbound_pinpoint_has_no_mention():
    citations, _ = extract("The tribunal turned to section 45 without more.")
    assert citations[0].mention is None
    assert citations[0].provisions == (Provision("section", "45"),)


def test_two_works_each_keep_their_own_pinpoint():
    citations, _ = extract(
        "section 6 of the Human Rights Act 1998 and section 172 of the Companies Act 2006"
    )
    assert len(citations) == 2
    assert citations[0].provisions == (Provision("section", "6"),)
    assert citations[1].provisions == (Provision("section", "172"),)


# -- anaphora --------------------------------------------------------------

def test_year_anaphor_is_found_sentence_initially():
    citations, _ = extract("The Theft Act 1968 applies. The 1968 Act was amended.")
    assert any(c.mention and c.mention.ref.anaphoric for c in citations)


def test_unknown_acronym_is_not_a_citation():
    citations, _ = extract("The EU and the USA disagreed.")
    assert citations == []


def test_defined_acronym_becomes_a_citation():
    citations, _ = extract('the Data Protection Act 2018 ("the DPA"). The DPA applies.')
    surfaces = [c.mention.ref.surface for c in citations if c.mention]
    assert "the DPA" in surfaces or "The DPA" in surfaces


# -- offsets ---------------------------------------------------------------

def test_offsets_index_the_original_string():
    text = "  See\tsection 6 of the Human Rights Act 1998 — and more.  "
    citations, _ = extract(text)
    for c in citations:
        assert text[c.start: c.end]
        assert c.start < c.end <= len(text)
