import json

import pytest

from legislink.models import CanonicalWork
from legislink.registry import Registry


def work(**kw):
    base = dict(id="ukpga-1998-42", title="Human Rights Act 1998",
                year=1998, work_type="ukpga", number="42")
    base.update(kw)
    return CanonicalWork(**base)


def test_seed_registry_loads():
    registry = Registry.default()
    assert len(registry) > 100
    assert registry.get("ukpga-1998-42").title == "Human Rights Act 1998"


def test_ids_are_unique():
    registry = Registry.default()
    assert len({w.id for w in registry}) == len(registry)


def test_lookup_by_title_needs_the_right_year():
    registry = Registry.default()
    assert registry.by_title("Companies Act", 2006)[0].id == "ukpga-2006-46"
    assert registry.by_title("Companies Act", 1985)[0].id == "ukpga-1985-6"


def test_lookup_by_number_is_year_scoped():
    registry = Registry.default()
    # Theft Act 1968 and PACE 1984 are both chapter 60.
    assert registry.by_number(1968, "60")[0].id == "ukpga-1968-60"
    assert registry.by_number(1984, "60")[0].id == "ukpga-1984-60"


def test_alias_lookup_is_punctuation_insensitive():
    registry = Registry.default()
    assert registry.by_alias("P.A.C.E.")[0].id == "ukpga-1984-60"
    assert registry.by_alias("the pace")[0].id == "ukpga-1984-60"


def test_alias_keys_are_normalised():
    keys = Registry.default().alias_keys()
    assert "pace" in keys and "tupe" in keys
    assert "PACE" not in keys


def test_adding_the_same_id_merges_aliases():
    registry = Registry()
    registry.add(work(aliases=("HRA",)))
    registry.add(work(aliases=("HRA 1998",)))
    assert len(registry) == 1
    assert set(registry.get("ukpga-1998-42").aliases) == {"HRA", "HRA 1998"}
    assert registry.by_alias("HRA 1998")[0].id == "ukpga-1998-42"


def test_merging_does_not_leave_a_stale_index_entry():
    registry = Registry()
    registry.add(work(aliases=("HRA",)))
    registry.add(work(aliases=("HRA 1998",)))
    assert len(registry.by_alias("HRA")) == 1
    assert len(registry.by_title("Human Rights Act", 1998)) == 1


def test_search_prefers_the_cited_year():
    registry = Registry.default()
    top = registry.search("Companies Act", 2006)[0]
    assert top[0].id == "ukpga-2006-46"


def test_search_penalises_a_different_year():
    registry = Registry.default()
    same = registry.search("Companies Act", 2006)[0][1]
    cross = registry.search("Companies Act", 1066)
    assert not cross or cross[0][1] < same


def test_load_file_extends_the_registry(tmp_path):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps({"works": [
        {"title": "Fictional Widgets Act 1911", "year": 1911,
         "work_type": "ukpga", "number": "7", "aliases": ["FWA"]}
    ]}), encoding="utf-8")

    registry = Registry.default()
    before = len(registry)
    assert registry.load_file(path) == 1
    assert len(registry) == before + 1
    assert registry.by_alias("FWA")[0].year == 1911


def test_env_var_is_read(tmp_path, monkeypatch):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps([
        {"title": "Fictional Widgets Act 1911", "year": 1911,
         "work_type": "ukpga", "number": "7"}
    ]), encoding="utf-8")
    monkeypatch.setenv("LEGISLINK_REGISTRY", str(path))
    assert Registry.default().get("ukpga-1911-7") is not None


@pytest.mark.parametrize(
    "work_id,expected",
    [
        ("ukpga-1968-60", "Theft Act 1968 (c. 60)"),
        ("uksi-2017-692", "SI 2017/692"),
        ("asp-2003-2", "(asp 2)"),
    ],
)
def test_conventional_citation_format(work_id, expected):
    assert expected in Registry.default().get(work_id).citation


def test_seed_data_has_no_obviously_broken_entries():
    for w in Registry.default():
        assert w.number and str(w.number).strip()
        assert 1200 < w.year <= 2100
        assert str(w.year) in w.title, f"{w.title} does not carry its year"
