import pytest

from legislink.models import CanonicalWork, Provision
from legislink.registry import Registry
from legislink.resolve import Resolver, infer_work_type


@pytest.fixture(scope="module")
def resolver():
    return Resolver(Registry.default())


def one(resolver, text):
    citations = resolver.resolve_document(text).citations
    assert citations, f"no citation found in {text!r}"
    return citations[0]


# -- strategies ------------------------------------------------------------

def test_exact_title(resolver):
    c = one(resolver, "the Human Rights Act 1998")
    assert c.work.id == "ukpga-1998-42"
    assert c.method == "exact"
    assert c.confidence == 1.0


def test_official_number_wins(resolver):
    c = one(resolver, "the Theft Act 1968 (c. 60)")
    assert c.work.id == "ukpga-1968-60"
    assert c.method == "number"


def test_number_resolves_a_work_absent_from_the_registry(resolver):
    c = one(resolver, "the Fictional Widgets Act 1911 (c. 7)")
    assert c.work is not None
    assert c.work.id == "ukpga-1911-7"
    assert c.method == "number"
    assert any("not in registry" in n for n in c.notes)


def test_bare_instrument_number(resolver):
    c = one(resolver, "see SI 2017/692")
    assert c.work.id == "uksi-2017-692"


def test_alias(resolver):
    c = one(resolver, "the court applied the PACE codes")
    assert c.work.id == "ukpga-1984-60"


def test_fuzzy_match_with_a_typo(resolver):
    c = one(resolver, "the Data Protecton Act 2018")
    assert c.work.id == "ukpga-2018-12"
    assert c.method == "fuzzy"
    assert 0.86 <= c.confidence < 1.0


def test_unknown_title_is_left_unresolved(resolver):
    c = one(resolver, "the Interplanetary Sandwich Act 1977")
    assert c.work is None
    assert c.method == "unresolved"
    assert c.url.startswith("https://www.legislation.gov.uk/all?")


def test_wrong_year_does_not_silently_match_another_year(resolver):
    # There is no Human Rights Act 1066; resolution must not snap to 1998.
    c = one(resolver, "the Human Rights Act 1066")
    assert c.work is None or c.work.year == 1066


# -- anaphora --------------------------------------------------------------

def test_short_form_binds_across_the_document(resolver):
    text = 'the Data Protection Act 2018 ("the DPA"). Section 45 of the DPA is engaged.'
    citations = resolver.resolve_document(text).citations
    last = citations[-1]
    assert last.work.id == "ukpga-2018-12"
    assert last.provisions == (Provision("section", "45"),)


def test_year_anaphor_points_at_the_earlier_work(resolver):
    text = "The Theft Act 1968 applies. The 1968 Act was later amended."
    citations = resolver.resolve_document(text).citations
    assert citations[-1].work.id == "ukpga-1968-60"
    assert citations[-1].method == "anaphora"


def test_bare_anaphor_prefers_the_last_act_over_the_last_instrument(resolver):
    text = "The Theft Act 1968 applies. SI 2017/692 was made. The Act is in force."
    citations = resolver.resolve_document(text).citations
    assert citations[-1].work.id == "ukpga-1968-60"


def test_bare_anaphor_picks_the_instrument_for_the_regulations(resolver):
    text = (
        "The Theft Act 1968 applies. The Working Time Regulations 1998 also apply. "
        "The Regulations were amended."
    )
    citations = resolver.resolve_document(text).citations
    assert citations[-1].work.id == "uksi-1998-1833"


def test_anaphor_with_no_antecedent_is_unresolved(resolver):
    c = one(resolver, "The Act is in force.")
    assert c.work is None


def test_loose_pinpoint_attaches_to_the_current_work(resolver):
    text = "The Companies Act 2006 applies. Section 172 imposes a duty."
    citations = resolver.resolve_document(text).citations
    assert citations[-1].work.id == "ukpga-2006-46"
    assert citations[-1].method == "context"
    assert citations[-1].confidence < 1.0


# -- work type inference ---------------------------------------------------

@pytest.mark.parametrize(
    "title,year,expected",
    [
        ("Some Act", 2000, "ukpga"),
        ("Some Regulations", 2000, "uksi"),
        ("Some (Scotland) Act", 2003, "asp"),
        ("Some (Scotland) Act", 1995, "ukpga"),
        ("Some (Scotland) Regulations", 2003, "ssi"),
        ("Some (Northern Ireland) Act", 2005, "nia"),
        ("Some (Wales) Act", 2021, "asc"),
        ("Some (Wales) Act", 2014, "anaw"),
    ],
)
def test_infer_work_type(title, year, expected):
    assert infer_work_type(title, year) == expected


# -- custom registries -----------------------------------------------------

def test_a_private_registry_resolves_private_works():
    registry = Registry()
    registry.add(
        CanonicalWork(
            id="ukpga-1911-7", title="Fictional Widgets Act 1911",
            year=1911, work_type="ukpga", number="7", aliases=("FWA",),
        )
    )
    resolver = Resolver(registry)
    c = one(resolver, "the Fictional Widgets Act 1911")
    assert c.work.id == "ukpga-1911-7"
    assert c.method == "exact"


def test_accept_threshold_is_honoured():
    registry = Registry.default()
    strict = Resolver(registry, accept=0.999)
    c = one(strict, "the Data Protecton Act 2018")
    assert c.work is None
    assert c.candidates  # near-misses are still reported
