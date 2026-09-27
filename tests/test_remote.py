"""The remote client's feed parsing, exercised with a stub HTTP client."""

from legislink.remote import LegislationGovUk

FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://www.legislation.gov.uk/id/ukpga/2020/21</id>
    <title>Agriculture Act 2020</title>
  </entry>
  <entry>
    <id>http://www.legislation.gov.uk/id/ukpga/1947/48</id>
    <title>Agriculture Act 1947</title>
  </entry>
</feed>"""


class StubResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class StubClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def client(text=FEED):
    return LegislationGovUk(client=StubClient(StubResponse(text)))


def test_finds_the_matching_year():
    work = client().find("Agriculture Act", 2020)
    assert work.id == "ukpga-2020-21"
    assert work.work_type == "ukpga"
    assert work.title == "Agriculture Act 2020"


def test_a_different_year_is_penalised():
    work = client().find("Agriculture Act", 1947)
    assert work.id == "ukpga-1947-48"


def test_an_unrelated_title_is_rejected():
    assert client().find("Interplanetary Sandwich Act", 2020) is None


def test_results_are_cached():
    remote = client()
    remote.find("Agriculture Act", 2020)
    remote.find("Agriculture Act", 2020)
    assert len(remote._client.calls) == 1


def test_the_year_is_sent_as_a_query_parameter():
    remote = client()
    remote.find("Agriculture Act", 2020)
    _, params = remote._client.calls[0]
    assert params == {"title": "Agriculture Act", "year": "2020"}


def test_a_network_failure_degrades_to_none():
    remote = LegislationGovUk(client=StubClient(RuntimeError("boom")))
    assert remote.find("Agriculture Act", 2020) is None


def test_malformed_xml_degrades_to_none():
    assert client("not xml at all").find("Agriculture Act", 2020) is None


def test_an_entry_without_a_usable_id_is_skipped():
    feed = FEED.replace("http://www.legislation.gov.uk/id/ukpga/2020/21", "urn:nonsense")
    assert client(feed).find("Agriculture Act", 2020).id == "ukpga-1947-48"


def test_a_remote_hit_is_folded_into_the_registry():
    from legislink.registry import Registry
    from legislink.resolve import Resolver

    registry = Registry()
    resolver = Resolver(registry, remote=client())
    citation = resolver.resolve_document("the Agriculture Act 2020 applies").citations[0]
    assert citation.method == "remote"
    assert citation.url == "https://www.legislation.gov.uk/ukpga/2020/21"
    assert registry.get("ukpga-2020-21") is not None
