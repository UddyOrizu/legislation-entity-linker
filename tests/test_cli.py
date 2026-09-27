import json

import pytest

from legislink.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_resolve_table(capsys):
    code, out = run(capsys, "resolve", "section 6 of the Human Rights Act 1998")
    assert code == 0
    assert "ukpga/1998/42/section/6" in out
    assert "1 citations, 1 resolved" in out


def test_resolve_json(capsys):
    code, out = run(capsys, "resolve", "--format", "json",
                    "section 6 of the Human Rights Act 1998")
    payload = json.loads(out)
    assert payload["citations"][0]["work"]["id"] == "ukpga-1998-42"
    assert "text" not in payload


def test_resolve_json_can_include_the_source_text(capsys):
    _, out = run(capsys, "resolve", "--format", "json", "--include-text",
                 "the Human Rights Act 1998")
    assert json.loads(out)["text"] == "the Human Rights Act 1998"


def test_resolve_reads_a_file(capsys, tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("The Theft Act 1968 applies.", encoding="utf-8")
    _, out = run(capsys, "resolve", "--file", str(path), "--format", "json")
    assert json.loads(out)["works"][0]["id"] == "ukpga-1968-60"


def test_a_positional_file_path_is_read_as_a_file(capsys, tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("The Theft Act 1968 applies.", encoding="utf-8")
    _, out = run(capsys, "resolve", str(path), "--format", "json")
    assert json.loads(out)["works"][0]["id"] == "ukpga-1968-60"


def test_positional_text_is_still_treated_as_text(capsys):
    _, out = run(capsys, "resolve", "--format", "json", "the Theft Act 1968")
    assert json.loads(out)["works"][0]["id"] == "ukpga-1968-60"


def test_resolve_reads_stdin(capsys, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("the Theft Act 1968"))
    _, out = run(capsys, "resolve", "--format", "json")
    assert json.loads(out)["works"][0]["id"] == "ukpga-1968-60"


def test_annotate_markdown(capsys):
    _, out = run(capsys, "annotate", "See the Human Rights Act 1998.")
    assert out == "See [the Human Rights Act 1998](https://www.legislation.gov.uk/ukpga/1998/42)." or \
           out == "See the [Human Rights Act 1998](https://www.legislation.gov.uk/ukpga/1998/42)."


def test_annotate_html(capsys):
    _, out = run(capsys, "annotate", "--html", "the Human Rights Act 1998")
    assert "<a href=" in out


def test_index_writes_json(capsys, tmp_path):
    doc = tmp_path / "a.txt"
    doc.write_text("Section 6 of the Human Rights Act 1998.", encoding="utf-8")
    out_path = tmp_path / "index.json"
    code, out = run(capsys, "index", str(tmp_path), "-o", str(out_path))
    assert code == 0
    assert "indexed 1 documents" in out
    assert json.loads(out_path.read_text())["stats"]["distinct_works"] == 1


def test_works_listing(capsys):
    _, out = run(capsys, "works", "--limit", "5")
    assert "works in registry" in out


def test_works_search(capsys):
    _, out = run(capsys, "works", "--search", "data protection")
    assert "ukpga-2018-12" in out


def test_extra_registry_is_honoured(capsys, tmp_path):
    reg = tmp_path / "extra.json"
    reg.write_text(json.dumps({"works": [
        {"title": "Fictional Widgets Act 1911", "year": 1911,
         "work_type": "ukpga", "number": "7"}
    ]}), encoding="utf-8")
    _, out = run(capsys, "resolve", "-r", str(reg), "--format", "json",
                 "the Fictional Widgets Act 1911")
    payload = json.loads(out)
    assert payload["citations"][0]["method"] == "exact"


def test_no_input_is_an_error(monkeypatch):
    class FakeStdin:
        def isatty(self):
            return True
    monkeypatch.setattr("sys.stdin", FakeStdin())
    with pytest.raises(SystemExit):
        main(["resolve"])


def test_version():
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
