"""High-level entry points: resolve a document, annotate it, index a corpus.

Each works over both legislation citations and case citations.
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Iterable, Iterator

from .models import CaseCitation, Citation, DocumentResult, Version
from .registry import Registry
from .resolve import Resolver

_DEFAULT: Resolver | None = None


def default_resolver() -> Resolver:
    """A lazily built resolver over the seed registry."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Resolver(Registry.default())
    return _DEFAULT


def resolve_text(
    text: str,
    resolver: Resolver | None = None,
    version: Version | None = None,
) -> DocumentResult:
    """Extract, resolve and link every citation in ``text``."""
    return (resolver or default_resolver()).resolve_document(text, version=version)


def annotate(
    result: DocumentResult,
    fmt: str = "markdown",
    min_confidence: float = 0.0,
) -> str:
    """Rewrite the source text with each citation turned into a link.

    Citations below ``min_confidence``, and those with no URL, are left as
    plain text -- an annotated document should not imply a link is
    authoritative when resolution was a guess.
    """
    if fmt not in ("markdown", "html"):
        raise ValueError(f"unknown format: {fmt!r}")

    pieces: list[str] = []
    cursor = 0
    text = result.text
    for citation in sorted([*result.citations, *result.cases], key=lambda c: c.start):
        if citation.start < cursor:
            continue  # overlapping match already consumed
        pieces.append(_escape(text[cursor: citation.start], fmt))
        surface = text[citation.start: citation.end]
        if citation.url and citation.confidence >= min_confidence:
            pieces.append(_link(surface, citation, fmt))
        else:
            pieces.append(_escape(surface, fmt))
        cursor = citation.end
    pieces.append(_escape(text[cursor:], fmt))
    return "".join(pieces)


def _escape(text: str, fmt: str) -> str:
    return _html.escape(text) if fmt == "html" else text


def _link(surface: str, citation: Citation | CaseCitation, fmt: str) -> str:
    if isinstance(citation, CaseCitation):
        title = citation.case.citation if citation.case else "unresolved"
    else:
        title = citation.work.citation if citation.work else "unresolved"
    if fmt == "html":
        return (
            f'<a href="{_html.escape(citation.url or "", quote=True)}"'
            f' title="{_html.escape(title, quote=True)}"'
            f' data-confidence="{citation.confidence:.2f}"'
            f' data-method="{citation.method}">{_html.escape(surface)}</a>'
        )
    safe = surface.replace("[", r"\[").replace("]", r"\]")
    return f"[{safe}]({citation.url})"


def iter_documents(paths: Iterable[str | Path], encoding: str = "utf-8") -> Iterator[tuple[Path, str]]:
    """Yield ``(path, text)`` for each file, expanding directories."""
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in (".txt", ".md", ".html", ".xml", ".json"):
                    yield child, child.read_text(encoding=encoding, errors="replace")
        else:
            yield path, path.read_text(encoding=encoding, errors="replace")


def build_index(
    paths: Iterable[str | Path],
    resolver: Resolver | None = None,
) -> dict:
    """Resolve a corpus and return a work-centric index.

    The index answers the question a citator is actually for: *which documents
    cite this piece of legislation, or this case, and at what pinpoint?*
    """
    resolver = resolver or default_resolver()
    works: dict[str, dict] = {}
    cases: dict[str, dict] = {}
    documents: list[dict] = []
    unresolved: dict[str, int] = {}
    unresolved_cases: dict[str, int] = {}

    for path, text in iter_documents(paths):
        result = resolver.resolve_document(text)
        documents.append(
            {
                "path": str(path),
                "citations": len(result.citations),
                "resolved": result.resolved_count,
                "works": [w.id for w in result.works()],
                "case_citations": len(result.cases),
                "cases_resolved": sum(1 for c in result.cases if c.resolved),
                "cases": [c.id for c in result.distinct_cases()],
            }
        )
        for citation in result.citations:
            if citation.work is None:
                key = citation.work_ref.title or citation.surface
                unresolved[key] = unresolved.get(key, 0) + 1
                continue
            entry = works.setdefault(
                citation.work.id,
                {**citation.work.to_dict(), "mentions": 0, "cited_by": {}},
            )
            entry["mentions"] += 1
            pinpoints = entry["cited_by"].setdefault(str(path), [])
            label = ", ".join(p.label for p in citation.provisions) or "(whole work)"
            if label not in pinpoints:
                pinpoints.append(label)
        for case_citation in result.cases:
            if case_citation.case is None:
                key = case_citation.case_ref.name or " ".join(case_citation.surface.split())
                unresolved_cases[key] = unresolved_cases.get(key, 0) + 1
                continue
            entry = cases.setdefault(
                case_citation.case.id,
                {**case_citation.case.to_dict(), "mentions": 0, "cited_by": {}},
            )
            entry["mentions"] += 1
            pinpoints = entry["cited_by"].setdefault(str(path), [])
            label = case_citation.case_ref.pinpoint or "(whole judgment)"
            if label not in pinpoints:
                pinpoints.append(label)

    return {
        "documents": documents,
        "works": sorted(works.values(), key=lambda w: -w["mentions"]),
        "unresolved": sorted(
            ({"reference": k, "count": v} for k, v in unresolved.items()),
            key=lambda u: -u["count"],
        ),
        "cases": sorted(cases.values(), key=lambda c: -c["mentions"]),
        "unresolved_cases": sorted(
            ({"reference": k, "count": v} for k, v in unresolved_cases.items()),
            key=lambda u: -u["count"],
        ),
        "stats": {
            "documents": len(documents),
            "distinct_works": len(works),
            "total_citations": sum(d["citations"] for d in documents),
            "resolved_citations": sum(d["resolved"] for d in documents),
            "distinct_cases": len(cases),
            "total_case_citations": sum(d["case_citations"] for d in documents),
            "resolved_case_citations": sum(d["cases_resolved"] for d in documents),
        },
    }
