# legislink

Extract UK legislation citations from text, resolve them to the actual piece of
legislation, and link them to [legislation.gov.uk](https://www.legislation.gov.uk).

```python
from legislink import resolve_text

result = resolve_text("The claimant relies on section 6 of the Human Rights Act 1998.")
c = result.citations[0]

c.work.citation   # 'Human Rights Act 1998 (c. 42)'
c.url             # 'https://www.legislation.gov.uk/ukpga/1998/42/section/6'
c.confidence      # 1.0
c.method          # 'exact'
```

The three jobs in the name are three separate passes, and each is usable on its
own:

| Pass | Module | Job |
|---|---|---|
| **Extract** | `legislink.extract` | Find citations in prose, with character offsets |
| **Resolve** | `legislink.resolve` | Decide *which* piece of legislation each one means |
| **Link** | `legislink.link` | Build the legislation.gov.uk URL and identifier |

## Install

```bash
pip install -e .              # library + CLI, no dependencies
pip install -e ".[api]"       # adds the REST API
pip install -e ".[dev]"       # everything, plus pytest
```

The core has **no required dependencies**. Optional extras: `fuzzy`
(rapidfuzz — better fuzzy matching; falls back to `difflib`), `remote`
(httpx — live lookups), `api` (FastAPI + uvicorn).

## What it understands

```
Human Rights Act 1998                          full title
Theft Act 1968 (c. 60)                         with chapter number
Land Reform (Scotland) Act 2003 (asp 2)        devolved legislation
Copyright, Designs and Patents Act 1988        titles containing commas
section 6 of the Human Rights Act 1998         forward binding
Companies Act 2006, ss 170-177                 backward binding, ranges
s 45(2)(a)                                     pinpoints with subdivisions
sections 1, 2 and 7                            lists
paragraph 4 of Schedule 2 to the Act           nested provisions
Part 3 / regulation 12 / article 6             other provision kinds
The Working Time Regulations 1998              statutory instruments
SI 2017/692                                    bare instrument numbers
the 1998 Act                                   year anaphora
the Act / the Regulations                      bare anaphora
the DPA  ("...Act 2018 ('the DPA')")           document-defined short forms
TUPE, PACE, COSHH                              registry-registered acronyms
```

Offsets always index the original string, so you can highlight or rewrite the
source document without re-aligning anything.

## Command line

The CLI accepts text directly, reads one or more files, or reads from standard
input. Use `--format json` when another program will consume the result.

```bash
legislink resolve "section 6 of the Human Rights Act 1998"
legislink resolve --file judgment.txt --format json
legislink resolve --as-at 1991-02-01 "section 2 of the European Communities Act 1972"
legislink resolve --original "section 2 of the European Communities Act 1972"
legislink annotate judgment.txt --html > judgment.html
legislink index corpus/ -o citation-index.json
legislink works --search "data protection"
legislink serve --port 8000
```

Common CLI options:

| Option | Purpose |
|---|---|
| `--file FILE` | Read the document from a file instead of a text argument |
| `--format table\|json` | Choose output format for `resolve` |
| `--include-text` | Include the source text in JSON output |
| `--html` | Emit HTML links from `annotate` instead of Markdown links |
| `--min-confidence SCORE` | Leave weaker matches unlinked when annotating |
| `-r, --registry FILE` | Load an additional registry JSON file; repeatable |
| `--remote` | Search legislation.gov.uk for unknown titles |
| `--accept SCORE` | Fuzzy score required for a resolved match |
| `--suggest SCORE` | Lower fuzzy score for reporting candidates |
| `--as-at YYYY-MM-DD` | Link to a point-in-time version |
| `--original` | Link to the original enacted or made version |
| `--prospective` | Explicitly request the latest available version |

Examples using files and pipelines:

```bash
legislink resolve --file judgment.txt --format json > result.json
cat judgment.txt | legislink resolve --format json
legislink annotate judgment.txt --html > judgment.html
legislink index judgments/ -o citation-index.json
legislink resolve --remote "the Agriculture Act 2020"
```

`resolve` prints a table by default:

```
CONF  METHOD  CITATION                                RESOLVED AS                 URL
----  ------  --------------------------------------  --------------------------  ---
1.00  exact   section 6 of the Human Rights Act 1998  Human Rights Act 1998 (c. 42)  https://…/ukpga/1998/42/section/6
```

`index` walks a corpus and inverts it into a citator: which documents cite this
Act, and at what pinpoint.

### Version selection

Version selection is a global per-call override. It does not try to infer a
date from phrases in the source document.

| Option | URL view |
|---|---|
| no option or `--prospective` | latest available version |
| `--original` | original version (`/enacted` for Acts) |
| `--as-at YYYY-MM-DD` | point-in-time version at the supplied date |

For example, `--as-at 1991-02-01` produces a URL such as:
`https://www.legislation.gov.uk/ukpga/1972/68/section/2/1991-02-01`.
The date is passed to legislation.gov.uk and is not live-validated by legislink.

## REST API

```bash
legislink serve --port 8000        # or: uvicorn legislink.api:app
```

The API is available at `http://127.0.0.1:8000` by default. It accepts JSON
requests and returns JSON responses.

Resolve citations:

```bash
curl -X POST http://127.0.0.1:8000/resolve \
  -H 'Content-Type: application/json' \
  -d '{"text":"Section 6 of the Human Rights Act 1998.","include_text":true}'
```

Select a historical or original version with the optional `version` field:

```bash
curl -X POST http://127.0.0.1:8000/resolve \
  -H 'Content-Type: application/json' \
  -d '{"text":"Section 2 of the European Communities Act 1972.","version":"as-at:1991-02-01"}'
```

Annotate text with links:

```bash
curl -X POST http://127.0.0.1:8000/annotate \
  -H 'Content-Type: application/json' \
  -d '{"text":"Section 6 of the Human Rights Act 1998.","format":"html","min_confidence":0.9}'
```

Search the registry and inspect service health:

```bash
curl 'http://127.0.0.1:8000/works?q=data%20protection&year=2018'
curl http://127.0.0.1:8000/works/ukpga-1998-42
curl http://127.0.0.1:8000/health
```

| Endpoint | Purpose |
|---|---|
| `POST /resolve` | `{"text": "...", "version": "as-at:1991-02-01"}` → citations, works, stats |
| `POST /annotate` | `{"text": "...", "format": "html", "version": "original"}` → linked text |
| `GET /works?q=…&year=…` | Search the registry |
| `GET /works/{id}` | One work |
| `GET /health` | Registry size, fuzzy backend, remote on/off |

The API accepts `latest`, `original`, `prospective`, or
`as-at:YYYY-MM-DD` as the `version` value. Omitting it preserves the default
latest-version behavior.

Interactive docs at `/docs`; the OpenAPI schema at `/openapi.json`.

## Python API

The Python API exposes the same extract, resolve, and link pipeline without
starting a server.

### Resolve text

```python
from legislink import resolve_text

result = resolve_text("Section 6 of the Human Rights Act 1998.")
for citation in result.citations:
  print(citation.work.citation if citation.work else "unresolved")
  print(citation.url)
```

Use `to_dict()` for JSON-compatible output:

```python
payload = result.to_dict(include_text=True)
print(payload["stats"])
```

### Configure resolution

```python
from legislink import Registry, Resolver, resolve_text

registry = Registry.default()
registry.load_file("my-works.json")
resolver = Resolver(
  registry,
  accept=0.90,
  suggest=0.65,
)
result = resolve_text("the Fictional Widgets Act 1911", resolver=resolver)
```

Enable optional live lookups explicitly:

```python
from legislink import Registry, Resolver, resolve_text
from legislink.remote import LegislationGovUk

resolver = Resolver(Registry.default(), remote=LegislationGovUk())
result = resolve_text("the Agriculture Act 2020", resolver=resolver)
```

### Version selection and annotation

```python
from legislink import Version, resolve_text
from legislink.pipeline import annotate

result = resolve_text(
  "Section 2 of the European Communities Act 1972.",
  version=Version(kind="point-in-time", date="1991-02-01"),
)
markdown = annotate(result, fmt="markdown", min_confidence=0.9)
html = annotate(result, fmt="html")
```

Use `Version(kind="original")` for the original version or
`Version(kind="latest")` for the current default behavior.

### Index a corpus

```python
from legislink import build_index

index = build_index(["judgments/", "reports/"], resolver=resolver)
print(index["stats"])
```

## How resolution decides

Strategies run strongest-evidence-first, and every citation reports which one
fired and how confident it is. Nothing resolves silently on weak evidence.

| `method` | Confidence | Basis |
|---|---|---|
| `number` | 0.95 – 1.00 | An official number was cited (`c. 60`, `SI 2017/692`) |
| `exact` | 0.90 – 1.00 | Normalised title and year match a registry entry |
| `alias` | 0.88 – 0.95 | A registered acronym (`PACE`, `TUPE`) |
| `short-form` | 0.92 | An acronym the document itself defined |
| `fuzzy` | ≥ `accept` (0.86) | Title similarity, e.g. an OCR error or typo |
| `remote` | 0.93 | Found on legislation.gov.uk (opt-in) |
| `anaphora` | 0.60 – 0.90 | "the 1998 Act", "the Act" → an earlier mention |
| `context` | 0.70 | A bare "section 45" attached to the current work |
| `unresolved` | 0.0 | Nothing cleared the bar — `url` is a search link |

Two deliberate choices worth knowing about:

- **A cited number always wins.** If the text says `(c. 60)`, the link is built
  from that even when the work isn't in the registry. You get a correct URL for
  legislation nobody pre-registered.
- **A wrong year does not snap to another year.** "Companies Act 1985" and
  "Companies Act 2006" are different Acts, so a same-title/different-year hit is
  reported in `candidates` and left *unresolved* rather than linked. In a legal
  tool a confidently wrong link is worse than no link.

Unresolved citations still get a `url` — a legislation.gov.uk search — but
`work` is `None` and `id_uri` is `None`, so resolved and unresolved are never
confusable.

## URLs and identifiers

Each citation carries two links:

```python
c.url      # https://www.legislation.gov.uk/ukpga/2018/12/section/45#section-45-2-a
c.id_uri   # https://www.legislation.gov.uk/id/ukpga/2018/12/section/45/2/a
```

`url` is the browsable page (subsection depth as a fragment, because the
section is the addressable page). `id_uri` is the abstract identifier — the one
to store in a database or triple store. When a version is selected, its suffix
is added to `url` only; `id_uri` remains version-free and continues to identify
the provision itself.

In Python, pass a `Version` to the resolver or pipeline:

```python
from legislink import Version, resolve_text

result = resolve_text(
  "section 2 of the European Communities Act 1972",
  version=Version(kind="point-in-time", date="1991-02-01"),
)
result.citations[0].url
# https://www.legislation.gov.uk/ukpga/1972/68/section/2/1991-02-01
```

Only Schedules contribute a path prefix. A Part or Chapter is dropped when
something more specific is cited, because legislation.gov.uk itself redirects
`/schedule/1/part/I/paragraph/1` → `/schedule/1/paragraph/1`.

When a citation names several provisions (`ss 1, 2 and 7`), `url` is the first
and `link.build()` returns all of them.

## Extending the registry

The shipped registry holds 178 frequently cited works, each one verified
against the live service (URL resolves, and the page title matches). It is a
starting point, not a complete corpus — extend it:

```bash
export LEGISLINK_REGISTRY=/path/to/my-works.json   # colon-separated
legislink resolve -r my-works.json "the Fictional Widgets Act 1911"
```

```json
{"works": [
  {"title": "Fictional Widgets Act 1911", "year": 1911,
   "work_type": "ukpga", "number": "7", "aliases": ["FWA"]}
]}
```

Or in code:

```python
from legislink import CanonicalWork, Registry, Resolver

registry = Registry.default()
registry.add(CanonicalWork(id="ukpga-1911-7", title="Fictional Widgets Act 1911",
                           year=1911, work_type="ukpga", number="7", aliases=("FWA",)))
resolver = Resolver(registry, accept=0.9)
```

Verify a registry file against the live service — a wrong number is the worst
failure this tool can have, so this is the ground truth for that mapping:

```bash
python scripts/verify_registry.py              # the seed registry
python scripts/verify_registry.py my-works.json
```

### Live lookups

Off by default. When on, an unresolved title is searched on legislation.gov.uk
and any hit is folded into the in-memory registry for the rest of the run:

```bash
legislink resolve --remote "the Agriculture Act 2020"
```

Network failures degrade to `unresolved` rather than raising.

## Tuning

```python
Resolver(registry, accept=0.86, suggest=0.60)
```

`accept` is the fuzzy score at which a match counts as resolved; `suggest` is
the lower bar at which a near-miss is still reported in `candidates` but left
unresolved. Raise `accept` for precision, lower it for recall. `annotate(...,
min_confidence=0.9)` leaves weaker citations as plain text.

## Limitations

- **UK only.** The citation grammar is jurisdiction-specific and does not
  transfer to US, EU or other citation styles.
- **Version selection is explicit.** Caller-supplied `latest`, `original`, or
  point-in-time dates select a legislation.gov.uk URL view; in-text dates are
  not detected, and amendments, repeals, commencement, and legal effect are
  not independently modelled.
- **No case citations.** `Smith v Jones [2019] UKSC 1` is deliberately ignored.
- **Fuzzy matching is bounded by the registry.** A work that is neither
  registered nor cited with its number stays unresolved unless `--remote` is on.
- The seed registry's chapter numbers were verified against legislation.gov.uk,
  but re-run `scripts/verify_registry.py` after editing it.

## Development

```bash
pip install -e ".[dev]"
pytest                    # 144 tests, no network required
```

The test suite is offline; only `scripts/verify_registry.py` touches the
network. There is a sample document to try things on:

```bash
legislink resolve examples/tribunal-judgment.txt     # 21/21 resolved
legislink annotate examples/tribunal-judgment.txt
```

```
legislink/
  patterns.py    the UK citation grammar (all regexes live here)
  extract.py     pass 1 — find citations, bind pinpoints to works
  registry.py    canonical works and their lookup indexes
  resolve.py     pass 2 — match references to works, resolve anaphora
  link.py        pass 3 — build URLs and identifiers
  pipeline.py    resolve / annotate / index a corpus
  normalize.py   title normalisation and similarity
  remote.py      optional legislation.gov.uk client
  cli.py  api.py interfaces
  data/works.json
scripts/verify_registry.py
examples/tribunal-judgment.txt
tests/
```

## Licence

MIT.
