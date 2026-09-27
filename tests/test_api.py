import pytest

fastapi = pytest.importorskip("fastapi", reason="needs the 'api' extra")
from fastapi.testclient import TestClient  # noqa: E402

from legislink.api import create_app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["registry_size"] > 100
    assert body["remote_lookups"] is False


def test_resolve(client):
    response = client.post("/resolve", json={"text": "section 6 of the Human Rights Act 1998"})
    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["resolved"] == 1
    assert body["citations"][0]["url"].endswith("/ukpga/1998/42/section/6")


def test_resolve_rejects_empty_text(client):
    assert client.post("/resolve", json={"text": ""}).status_code == 422


def test_annotate(client):
    body = client.post(
        "/annotate", json={"text": "the Human Rights Act 1998", "format": "html"}
    ).json()
    assert body["format"] == "html"
    assert "<a href=" in body["annotated"]


def test_annotate_rejects_an_unknown_format(client):
    response = client.post(
        "/annotate", json={"text": "the Human Rights Act 1998", "format": "latex"}
    )
    assert response.status_code == 422


def test_works_search(client):
    body = client.get("/works", params={"q": "data protection"}).json()
    assert body["results"][0]["id"] == "ukpga-2018-12"
    assert 0 < body["results"][0]["score"] <= 1


def test_works_filtered_by_year(client):
    body = client.get("/works", params={"year": 1998, "limit": 100}).json()
    assert body["total"] > 0
    assert all(w["year"] == 1998 for w in body["results"])


def test_work_by_id(client):
    body = client.get("/works/ukpga-1998-42").json()
    assert body["title"] == "Human Rights Act 1998"
    assert body["url"] == "https://www.legislation.gov.uk/ukpga/1998/42"


def test_unknown_work_is_404(client):
    assert client.get("/works/ukpga-9999-1").status_code == 404


def test_openapi_schema_is_generated(client):
    schema = client.get("/openapi.json").json()
    assert "/resolve" in schema["paths"]
    assert "/works/{work_id}" in schema["paths"]
