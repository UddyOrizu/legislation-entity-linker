import json

import pytest

from legislink import CaseRegistry, Resolver, annotate, find_cases, resolve_text
from legislink.cases import parse_neutral, parse_report
from legislink.cli import main
from legislink.courts import case_id, judgment_url
from legislink.models import CanonicalCase
from legislink.pipeline import build_index

TNA = "https://caselaw.nationalarchives.gov.uk"


def only_case(text, **kwargs):
    result = resolve_text(text, **kwargs)
    assert len(result.cases) == 1, [c.surface for c in result.cases]
    return result.cases[0]


# -- extraction ------------------------------------------------------------

def test_extracts_name_neutral_report_and_pinpoint():
    [m] = find_cases("See R (Miller) v Prime Minister [2019] UKSC 41, [2020] AC 373 at [30]-[35].")
    assert m.ref.name == "R (Miller) v Prime Minister"
    assert m.ref.neutral.text == "[2019] UKSC 41"
    assert [r.text for r in m.ref.reports] == ["[2020] AC 373"]
    assert m.ref.pinpoint == "[30]-[35]"


def test_extraction_offsets_index_the_original_text():
    text = "As held in Patel v Mirza [2016] UKSC 42, illegality is a defence."
    [m] = find_cases(text)
    assert text[m.start: m.end] == "Patel v Mirza [2016] UKSC 42"


@pytest.mark.parametrize("cite, court, division", [
    ("[2019] EWHC 123 (Ch)", "EWHC", "Ch"),
    ("[2020] EWCA Civ 1", "EWCA Civ", None),
    ("[2020] EWCA Crim 10", "EWCA Crim", None),
    ("[2026] EWFC 290 (B)", "EWFC", "B"),
    ("[2026] EWCOP 52 (T2)", "EWCOP", "T2"),
    ("[2020] UKUT 5 (IAC)", "UKUT", "IAC"),
    ("[2022] EAT 1", "EAT", None),
    ("[2020] CSIH 1", "CSIH", None),
])
def test_neutral_citation_forms(cite, court, division):
    [m] = find_cases(f"The decision in {cite} was followed.")
    assert m.ref.neutral.court == court
    assert m.ref.neutral.division == division
    assert m.ref.neutral.text == cite


@pytest.mark.parametrize("cite", [
    "[1932] AC 562",
    "[2004] 2 AC 557",
    "[2017] 1 WLR 1343",
    "[2003] 1 All ER 1",
    "(2002) 35 EHRR 1",
    "[2000] 2 Cr App R 1",
    "[2018] ICR 1511",
    "[1996] Ch 1",
])
def test_law_report_forms(cite):
    [m] = find_cases(f"It was reported at {cite} in full.")
    assert [r.text for r in m.ref.reports] == [cite]


def test_unknown_report_series_is_not_a_citation():
    assert find_cases("See [2004] 2 XYZ 557 and [2019] 1 Foo 3.") == []


def test_sentence_words_are_not_swept_into_the_name():
    [m] = find_cases("Applying Donoghue v Stevenson [1932] AC 562, the claim succeeds.")
    assert m.ref.name == "Donoghue v Stevenson"


def test_name_stops_at_a_sentence_boundary():
    [m] = find_cases("The appeal failed. Patel v Mirza [2016] UKSC 42 was distinguished.")
    assert m.ref.name == "Patel v Mirza"


def test_ex_parte_and_corporate_names():
    [a] = find_cases("R v Secretary of State for the Home Department, ex p. Brind [1991] 1 AC 696.")
    assert a.ref.name == "R v Secretary of State for the Home Department, ex p. Brind"
    [b] = find_cases("Caparo Industries plc v Dickman [1990] UKHL 2.")
    assert b.ref.name == "Caparo Industries plc v Dickman"


def test_re_cases():
    [m] = find_cases("In Re B (A Child) [2013] UKSC 33 the court held.")
    assert m.ref.name == "Re B (A Child)"


def test_a_second_neutral_citation_is_a_separate_case():
    found = find_cases("Smith v Jones [2018] EWCA Civ 1, [2019] UKSC 2.")
    assert [m.ref.neutral.text for m in found] == ["[2018] EWCA Civ 1", "[2019] UKSC 2"]


def test_bare_case_names_need_a_registered_name():
    known = CaseRegistry.default().name_keys()
    text = "Following Patel v Mirza and Montgomery v Lanarkshire Health Board, the Brexit v Remain debate."
    names = [m.ref.name for m in find_cases(text, known)]
    assert names == ["Patel v Mirza", "Montgomery v Lanarkshire Health Board"]
    assert find_cases(text) == []  # no vocabulary, no name-only mentions


# -- courts and URLs -------------------------------------------------------

@pytest.mark.parametrize("cite, url", [
    ("[2019] UKSC 41", f"{TNA}/uksc/2019/41"),
    ("[2020] EWCA Civ 1", f"{TNA}/ewca/civ/2020/1"),
    ("[2019] EWHC 1 (Ch)", f"{TNA}/ewhc/ch/2019/1"),
    ("[2019] EWHC 1 (ch)", f"{TNA}/ewhc/ch/2019/1"),
    ("[2026] EWFC 290 (B)", f"{TNA}/ewfc/b/2026/290"),
    ("[2020] EWCOP 1", f"{TNA}/ewcop/2020/1"),
    ("[2026] EWCOP 52 (T2)", f"{TNA}/ewcop/t2/2026/52"),
    ("[2022] EAT 1", f"{TNA}/eat/2022/1"),
    ("[1932] UKHL 100", "https://www.bailii.org/uk/cases/UKHL/1932/100.html"),
    ("[1947] EWCA Civ 1", "https://www.bailii.org/ew/cases/EWCA/Civ/1947/1.html"),
    ("[2020] CSIH 1", "https://www.bailii.org/scot/cases/ScotCS/2020/2020_CSIH_1.html"),
])
def test_judgment_urls(cite, url):
    assert judgment_url(parse_neutral(cite))[0] == url


@pytest.mark.parametrize("cite", ["[2019] EWHC 123", "[2019] EWHC 123 (Costs)", "[2020] NIQB 5"])
def test_no_url_is_guessed_for_unknown_shapes(cite):
    assert judgment_url(parse_neutral(cite)) == (None, None)


def test_case_ids():
    assert case_id(parse_neutral("[2019] UKSC 41")) == "uksc-2019-41"
    assert case_id(parse_neutral("[2019] EWHC 123 (Ch)")) == "ewhc-ch-2019-123"
    assert case_id(parse_neutral("[2020] EWCA Civ 1")) == "ewca-civ-2020-1"


# -- resolution ------------------------------------------------------------

def test_registered_neutral_citation():
    c = only_case("R (Miller) v Prime Minister [2019] UKSC 41")
    assert c.method == "neutral" and c.confidence == 1.0
    assert c.case.id == "uksc-2019-41"
    assert c.url == f"{TNA}/uksc/2019/41" and c.source == "tna"


def test_unregistered_neutral_citation_is_constructed():
    c = only_case("Smith v Jones [2020] EWCA Civ 1")
    assert c.resolved and c.method == "neutral" and c.confidence == 0.95
    assert c.case.name == "Smith v Jones"
    assert c.url == f"{TNA}/ewca/civ/2020/1"
    assert "not in registry" in c.notes[0]


def test_report_citation_resolves_through_the_registry():
    c = only_case("Patel v Mirza [2017] AC 467 at [120]")
    assert c.method == "report" and c.confidence == 0.95
    assert c.url == f"{TNA}/uksc/2016/42"
    assert c.case_ref.pinpoint == "[120]"


def test_report_citation_alone_is_weaker():
    c = only_case("as reported at [2020] AC 373.")
    assert c.case.id == "uksc-2019-41"
    assert c.confidence == 0.85


def test_report_volume_may_be_omitted():
    c = only_case("Essop v Home Office [2017] WLR 1343")
    assert c.case.id == "uksc-2017-27"
    assert "matched ignoring the report volume" in c.notes


def test_a_report_citation_does_not_override_a_conflicting_name():
    c = only_case("Lachaux v Smith [2020] AC 612")
    assert not c.resolved
    assert c.candidates[0][0] == "uksc-2019-27"
    assert "does not match" in c.notes[0]


def test_case_cited_by_name_alone():
    c = only_case("The principle in Montgomery v Lanarkshire Health Board applies.")
    assert c.method == "name"
    assert c.url == f"{TNA}/uksc/2015/11"


def test_name_match_requires_agreeing_years():
    # Right name, but a report from a decade earlier: a different judgment.
    c = only_case("Patel v Mirza [2006] AC 1")
    assert not c.resolved


def test_unresolved_case_links_to_search():
    c = only_case("Donoghue v Stevenson [1932] AC 562")
    assert not c.resolved and c.case is None
    assert c.source == "search"
    assert c.url == f"{TNA}/search?query=Donoghue+v+Stevenson"


def test_unlinkable_court_gets_no_url():
    c = only_case("Re X [2010] NIQB 5")
    assert c.resolved and c.url is None
    assert "no direct link for NIQB" in c.notes


def test_extra_case_registry():
    registry = CaseRegistry.default()
    registry.add(CanonicalCase(id="x", name="Donoghue v Stevenson",
                               neutral=parse_neutral("[1932] UKHL 100"),
                               reports=(parse_report("[1932] AC 562").text,)))
    c = only_case("Donoghue v Stevenson [1932] AC 562", resolver=Resolver(case_registry=registry))
    assert c.method == "report"
    assert c.url == "https://www.bailii.org/uk/cases/UKHL/1932/100.html"


def test_registry_loads_json(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [
        {"name": "Carlill v Carbolic Smoke Ball Co", "neutral": "[1892] EWCA Civ 1",
         "reports": ["[1893] 1 QB 256"]},
    ]}), encoding="utf-8")
    registry = CaseRegistry.from_file(path)
    [case] = registry.by_name("Carlill v Carbolic Smoke Ball Co")
    assert case.id == "ewca-civ-1892-1"
    assert registry.by_report(parse_report("[1893] 1 QB 256"))[0] == [case]


def test_seed_registry_is_well_formed():
    registry = CaseRegistry.default()
    assert len(registry) >= 10
    for case in registry:
        assert case.neutral is not None and case.url.startswith(TNA)


# -- interaction with legislation ------------------------------------------

def test_law_reports_are_not_read_as_legislation():
    result = resolve_text("Smith v Jones [1996] Ch 1, at para 45, and the Human Rights Act 1998.")
    assert [c.surface for c in result.citations] == ["Human Rights Act 1998"]
    assert result.cases[0].case_ref.pinpoint == "para 45"


def test_link_cases_false_restores_legislation_only_behaviour():
    result = resolve_text("Patel v Mirza [2016] UKSC 42", resolver=Resolver(link_cases=False))
    assert result.cases == []


def test_document_json_reports_cases():
    payload = resolve_text(
        "Section 6 of the Human Rights Act 1998; Patel v Mirza [2016] UKSC 42."
    ).to_dict()
    assert payload["stats"]["citations"] == 1
    assert payload["stats"]["case_citations"] == 1
    assert payload["stats"]["distinct_cases"] == 1
    assert payload["cases"][0]["case"]["id"] == "uksc-2016-42"
    assert payload["case_law"][0]["neutral"] == "[2016] UKSC 42"


def test_annotate_links_cases_and_legislation():
    text = "Patel v Mirza [2016] UKSC 42 and section 6 of the Human Rights Act 1998."
    out = annotate(resolve_text(text))
    assert f"]({TNA}/uksc/2016/42)" in out
    assert "](https://www.legislation.gov.uk/ukpga/1998/42/section/6)" in out
    import re
    assert re.sub(r"\[((?:\\.|[^\]])+)\]\([^)]+\)", r"\1", out).replace("\\", "") == text


def test_annotate_html_titles_cases():
    out = annotate(resolve_text("Patel v Mirza [2016] UKSC 42"), fmt="html")
    assert 'title="Patel v Mirza [2016] UKSC 42, [2017] AC 467"' in out
    assert 'data-method="neutral"' in out


def test_index_groups_cases(tmp_path):
    (tmp_path / "a.txt").write_text("Patel v Mirza [2016] UKSC 42 at [99].", encoding="utf-8")
    (tmp_path / "b.txt").write_text("See [2017] AC 467. Donoghue v Stevenson [1932] AC 562.",
                                    encoding="utf-8")
    index = build_index([tmp_path])
    [patel] = index["cases"]
    assert patel["id"] == "uksc-2016-42"
    assert patel["mentions"] == 2
    assert sorted(p for v in patel["cited_by"].values() for p in v) == ["(whole judgment)", "[99]"]
    assert index["unresolved_cases"] == [{"reference": "Donoghue v Stevenson", "count": 1}]
    assert index["stats"]["resolved_case_citations"] == 2


# -- interfaces ------------------------------------------------------------

def test_cli_resolve_prints_a_case_table(capsys):
    assert main(["resolve", "Patel v Mirza [2016] UKSC 42"]) == 0
    out = capsys.readouterr().out
    assert "uksc/2016/42" in out
    assert "1 case citations, 1 resolved" in out


def test_cli_no_cases_flag(capsys):
    main(["resolve", "--format", "json", "--no-cases", "Patel v Mirza [2016] UKSC 42"])
    assert json.loads(capsys.readouterr().out)["cases"] == []


def test_cli_extra_case_file(capsys, tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [
        {"name": "Donoghue v Stevenson", "neutral": "[1932] UKHL 100", "reports": ["[1932] AC 562"]},
    ]}), encoding="utf-8")
    main(["resolve", "--format", "json", "--cases", str(path), "Donoghue v Stevenson [1932] AC 562"])
    assert json.loads(capsys.readouterr().out)["cases"][0]["case"]["id"] == "ukhl-1932-100"


def test_cli_cases_search(capsys):
    assert main(["cases", "--search", "miller"]) == 0
    assert "uksc-2019-41" in capsys.readouterr().out


def test_cli_accepts_long_text_arguments(capsys):
    long_text = "Patel v Mirza [2016] UKSC 42. " * 20
    assert main(["resolve", long_text]) == 0


def test_api_cases():
    pytest.importorskip("fastapi", reason="needs the 'api' extra")
    from fastapi.testclient import TestClient
    from legislink.api import create_app

    client = TestClient(create_app())
    assert client.get("/health").json()["case_registry_size"] >= 10
    body = client.post("/resolve", json={"text": "Patel v Mirza [2016] UKSC 42"}).json()
    assert body["cases"][0]["url"] == f"{TNA}/uksc/2016/42"
    assert client.get("/cases", params={"q": "montgomery"}).json()["results"][0]["id"] == "uksc-2015-11"
    assert client.get("/cases/uksc-2016-42").json()["name"] == "Patel v Mirza"
    assert client.get("/cases/nope").status_code == 404
