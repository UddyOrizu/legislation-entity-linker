"""Core data types for legislation citation extraction, resolution and linking."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

BASE = "https://www.legislation.gov.uk"

#: legislation.gov.uk document type codes we understand.
WORK_TYPES = {
    "ukpga": "UK Public General Act",
    "ukla": "UK Local Act",
    "uksi": "UK Statutory Instrument",
    "asp": "Act of the Scottish Parliament",
    "ssi": "Scottish Statutory Instrument",
    "nia": "Act of the Northern Ireland Assembly",
    "nisr": "Northern Ireland Statutory Rule",
    "anaw": "Act of the National Assembly for Wales",
    "asc": "Act of Senedd Cymru",
    "wsi": "Wales Statutory Instrument",
    "ukcm": "Church Measure",
}

#: Provision kinds, mapped to the path segment legislation.gov.uk uses.
PROVISION_PATHS = {
    "section": "section",
    "schedule": "schedule",
    "part": "part",
    "chapter": "chapter",
    "regulation": "regulation",
    "article": "article",
    "rule": "rule",
    "paragraph": "paragraph",
    "crossheading": "crossheading",
}

#: Kinds that can contain other provisions, ordered outermost-first. Used to
#: nest "paragraph 4 of Schedule 2" into a single /schedule/2/paragraph/4 path.
CONTAINER_KINDS = ("part", "chapter", "schedule")


@dataclass(frozen=True)
class Provision:
    """A pinpoint reference within a work, e.g. ``s 45(2)(a)``.

    ``subdivisions`` holds the bracketed levels below the numbered provision
    (subsection, then paragraph, then sub-paragraph), which legislation.gov.uk
    addresses with a URL fragment rather than a path segment.
    """

    kind: str
    number: str
    subdivisions: tuple[str, ...] = ()
    end_number: str | None = None  # set for ranges, e.g. "sections 45 to 48"

    @property
    def label(self) -> str:
        text = f"{self.kind} {self.number}"
        text += "".join(f"({s})" for s in self.subdivisions)
        if self.end_number:
            text += f" to {self.end_number}"
        return text

    @property
    def path(self) -> tuple[str, ...]:
        """Path segments for this provision, e.g. ``("section", "45")``."""
        return (PROVISION_PATHS.get(self.kind, self.kind), self.number)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkRef:
    """How a work was referred to *in the text*, before resolution."""

    surface: str
    title: str | None = None
    year: int | None = None
    number: str | None = None  # chapter number, SI number, asp number...
    work_type: str | None = None
    anaphoric: bool = False  # "the 1998 Act", "the Regulations", "the DPA"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Version:
    """A caller-selected legislation.gov.uk version view.

    ``kind`` chooses the URL suffix to append after the provision path.
    ``latest`` keeps the current behaviour; ``original`` adds ``/enacted``;
    ``point-in-time`` adds ``/{date}`` for a specific as-at date.
    """

    kind: str = "latest"
    date: str | None = None

    @property
    def suffix(self) -> str:
        if self.kind == "original":
            return "/enacted"
        if self.kind == "point-in-time" and self.date:
            return f"/{self.date}"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CanonicalWork:
    """A single, authoritative piece of legislation in the registry."""

    id: str
    title: str
    year: int
    work_type: str
    number: str
    aliases: tuple[str, ...] = ()

    @property
    def url(self) -> str:
        return f"{BASE}/{self.work_type}/{self.year}/{self.number}"

    @property
    def id_uri(self) -> str:
        return f"{BASE}/id/{self.work_type}/{self.year}/{self.number}"

    @property
    def citation(self) -> str:
        """Conventional citation, e.g. ``Theft Act 1968 (c. 60)``."""
        if self.work_type in ("ukpga", "ukla"):
            return f"{self.title} (c. {self.number})"
        if self.work_type in ("uksi", "ssi", "nisr", "wsi"):
            code = {"uksi": "SI", "ssi": "SSI", "nisr": "SR", "wsi": "WSI"}[self.work_type]
            return f"{self.title} ({code} {self.year}/{self.number})"
        if self.work_type in ("asp", "anaw", "asc", "nia"):
            return f"{self.title} ({self.work_type} {self.number})"
        return self.title

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["aliases"] = list(self.aliases)
        d["url"] = self.url
        d["citation"] = self.citation
        return d


@dataclass
class Citation:
    """An extracted citation, with whatever resolution succeeded."""

    surface: str
    start: int
    end: int
    work_ref: WorkRef
    provisions: tuple[Provision, ...] = ()
    work: CanonicalWork | None = None
    confidence: float = 0.0
    method: str = "unresolved"
    candidates: tuple[tuple[str, float], ...] = ()
    notes: tuple[str, ...] = ()
    url: str | None = None
    id_uri: str | None = None
    version: Version | None = None

    @property
    def resolved(self) -> bool:
        return self.work is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "start": self.start,
            "end": self.end,
            "work_ref": self.work_ref.to_dict(),
            "provisions": [p.to_dict() for p in self.provisions],
            "provision_label": ", ".join(p.label for p in self.provisions) or None,
            "work": self.work.to_dict() if self.work else None,
            "resolved": self.resolved,
            "confidence": round(self.confidence, 4),
            "method": self.method,
            "candidates": [{"id": i, "score": round(s, 4)} for i, s in self.candidates],
            "notes": list(self.notes),
            "url": self.url,
            "id_uri": self.id_uri,
            "version": self.version.to_dict() if self.version else None,
        }


@dataclass
class DocumentResult:
    """Everything found in one document."""

    text: str
    citations: list[Citation] = field(default_factory=list)

    @property
    def resolved_count(self) -> int:
        return sum(1 for c in self.citations if c.resolved)

    def works(self) -> list[CanonicalWork]:
        """Distinct resolved works, in order of first mention."""
        seen: dict[str, CanonicalWork] = {}
        for c in self.citations:
            if c.work and c.work.id not in seen:
                seen[c.work.id] = c.work
        return list(seen.values())

    def to_dict(self, include_text: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "citations": [c.to_dict() for c in self.citations],
            "works": [w.to_dict() for w in self.works()],
            "stats": {
                "citations": len(self.citations),
                "resolved": self.resolved_count,
                "unresolved": len(self.citations) - self.resolved_count,
                "distinct_works": len(self.works()),
            },
        }
        if include_text:
            d["text"] = self.text
        return d
