import pytest

from legislink.link import build, search_url, targets
from legislink.models import CanonicalWork, Provision, Version

ACT = CanonicalWork(id="ukpga-1998-42", title="Human Rights Act 1998",
                    year=1998, work_type="ukpga", number="42")
SI = CanonicalWork(id="uksi-2017-692", title="The Money Laundering Regulations 2017",
                   year=2017, work_type="uksi", number="692")


def url_for(*chain):
    return build(ACT, tuple(chain))[0]


def test_whole_work():
    assert build(ACT, ())[0] == "https://www.legislation.gov.uk/ukpga/1998/42"


def test_section():
    assert url_for(Provision("section", "6")).endswith("/section/6")


def test_subsection_uses_a_fragment():
    assert url_for(Provision("section", "45", ("2", "a"))) == (
        "https://www.legislation.gov.uk/ukpga/1998/42/section/45#section-45-2-a"
    )


def test_subsection_id_uri_uses_path_segments():
    _, id_uri, _ = build(ACT, (Provision("section", "45", ("2", "a")),))
    assert id_uri == "https://www.legislation.gov.uk/id/ukpga/1998/42/section/45/2/a"


def test_schedule_paragraph_nests():
    assert url_for(Provision("schedule", "2"), Provision("paragraph", "4")).endswith(
        "/schedule/2/paragraph/4"
    )


def test_part_inside_a_schedule_is_dropped():
    # legislation.gov.uk redirects /schedule/1/part/I/paragraph/1 to
    # /schedule/1/paragraph/1, so the Part must not appear in the path.
    url = url_for(
        Provision("schedule", "1"), Provision("part", "2"), Provision("paragraph", "4")
    )
    assert url.endswith("/schedule/1/paragraph/4")
    assert "/part/" not in url


def test_part_alone_is_addressable():
    assert url_for(Provision("part", "3")).endswith("/part/3")


def test_part_of_a_schedule_falls_back_to_the_schedule():
    url = url_for(Provision("schedule", "1"), Provision("part", "2"))
    assert url.endswith("/schedule/1")


def test_section_cited_via_its_part_links_to_the_section():
    url = url_for(Provision("part", "2"), Provision("section", "6"))
    assert url.endswith("/section/6")


def test_sibling_provisions_produce_one_url_each():
    primary, _, all_urls = build(
        ACT, (Provision("section", "1"), Provision("section", "2"))
    )
    assert len(all_urls) == 2
    assert primary == all_urls[0]
    assert all_urls[1].endswith("/section/2")


def test_range_links_to_the_first_provision():
    assert url_for(Provision("section", "170", (), "177")).endswith("/section/170")


def test_instrument_regulation():
    assert build(SI, (Provision("regulation", "12"),))[0].endswith(
        "/uksi/2017/692/regulation/12"
    )


def test_unresolved_work_gets_a_search_url():
    url, id_uri, all_urls = build(None, (), title="Nonexistent Act", year=1999)
    assert id_uri is None
    assert all_urls == []
    assert "title=Nonexistent+Act" in url and "year=1999" in url


def test_point_in_time_version_adds_a_date_segment_to_the_url():
    version = Version(kind="point-in-time", date="1991-02-01")
    assert build(ACT, (Provision("section", "2"),), version=version)[0] == (
        "https://www.legislation.gov.uk/ukpga/1998/42/section/2/1991-02-01"
    )


def test_original_version_uses_enacted_suffix():
    version = Version(kind="original")
    assert build(ACT, (Provision("section", "6"),), version=version)[0].endswith(
        "/section/6/enacted"
    )


def test_search_url_without_a_year():
    assert search_url("Theft Act") == "https://www.legislation.gov.uk/all?title=Theft+Act"


def test_targets_of_an_empty_chain():
    assert targets(()) == []
