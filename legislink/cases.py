"""The case registry: known judgments and their lookup indexes.

A neutral citation identifies a judgment on its own, so the registry is not
needed to link one. It exists for the other ways a case is cited -- by a law
report reference (``[2020] AC 373``) or by name alone (``Patel v Mirza``) --
which carry no URL of their own.

It ships with a small seed whose neutral citations were checked against Find
Case Law, and is extensible: point ``LEGISLINK_CASES`` at a JSON file, or call
:meth:`CaseRegistry.add` / :meth:`CaseRegistry.load_file` at runtime.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator

from .courts import case_id, make_neutral
from .models import CanonicalCase, NeutralCitation, ReportCitation
from .normalize import normalize_case_name, similarity
from . import patterns as P

DATA_DIR = Path(__file__).parent / "data"
SEED_FILE = DATA_DIR / "cases.json"
ENV_VAR = "LEGISLINK_CASES"


def parse_neutral(text: str) -> NeutralCitation:
    """Parse ``"[2019] EWHC 123 (Ch)"``; raises ``ValueError`` if it is not one."""
    m = P.NEUTRAL_RE.fullmatch(text.strip())
    if not m:
        raise ValueError(f"not a neutral citation: {text!r}")
    return make_neutral(m.group("year"), m.group("court"), m.group("number"), m.group("division"))


def parse_report(text: str) -> ReportCitation:
    """Parse ``"[2004] 2 AC 557"``; raises ``ValueError`` if it is not one."""
    m = P.REPORT_RE.fullmatch(text.strip())
    if not m:
        raise ValueError(f"not a law report citation: {text!r}")
    return report_from_match(m)


def report_from_match(m: re.Match) -> ReportCitation:
    return ReportCitation(
        year=int(m.group("year")),
        series=" ".join(m.group("series").split()).replace("’", "'"),
        page=m.group("page"),
        volume=m.group("volume"),
        square=m.group("open") == "[",
    )


def report_key(report: ReportCitation, with_volume: bool = True) -> str:
    """Comparison key: ``[2004] 2 AC 557`` -> ``"2004|2|ac|557"``."""
    series = re.sub(r"[^a-z0-9&]", "", report.series.lower())
    volume = (report.volume or "") if with_volume else "*"
    return f"{report.year}|{volume}|{series}|{report.page}"


class CaseRegistry:
    """An in-memory index of known cases."""

    def __init__(self, cases: Iterable[CanonicalCase] = ()) -> None:
        self._by_id: dict[str, CanonicalCase] = {}
        self._by_neutral: dict[str, CanonicalCase] = {}
        self._by_report: dict[str, list[CanonicalCase]] = defaultdict(list)
        self._by_report_loose: dict[str, list[CanonicalCase]] = defaultdict(list)
        self._by_name: dict[str, list[CanonicalCase]] = defaultdict(list)
        for case in cases:
            self.add(case)

    # -- construction ------------------------------------------------------

    @classmethod
    def default(cls) -> "CaseRegistry":
        """Seed registry, plus anything named by ``LEGISLINK_CASES``."""
        registry = cls.from_file(SEED_FILE)
        extra = os.environ.get(ENV_VAR)
        if extra:
            for part in extra.split(os.pathsep):
                if part.strip():
                    registry.load_file(Path(part.strip()))
        return registry

    @classmethod
    def from_file(cls, path: str | Path) -> "CaseRegistry":
        registry = cls()
        registry.load_file(path)
        return registry

    def load_file(self, path: str | Path) -> int:
        """Merge cases from a JSON file. Returns the number added."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = payload["cases"] if isinstance(payload, dict) else payload
        count = 0
        for entry in entries:
            self.add(self._coerce(entry))
            count += 1
        return count

    @staticmethod
    def _coerce(entry: dict) -> CanonicalCase:
        neutral = parse_neutral(entry["neutral"]) if entry.get("neutral") else None
        reports = tuple(parse_report(r).text for r in entry.get("reports", ()))
        if neutral is None and not reports and not entry.get("id"):
            raise ValueError(f"case needs a neutral citation, a report or an id: {entry!r}")
        return CanonicalCase(
            id=entry.get("id") or (case_id(neutral) if neutral else _report_id(reports[0])),
            name=entry["name"],
            neutral=neutral,
            reports=reports,
            aliases=tuple(entry.get("aliases", ())),
        )

    def add(self, case: CanonicalCase) -> CanonicalCase:
        """Add a case, replacing any existing entry with the same id."""
        if case.id in self._by_id:
            self._forget(self._by_id[case.id])
        self._by_id[case.id] = case
        if case.neutral:
            self._by_neutral[case.neutral.text.lower()] = case
        for text in case.reports:
            report = parse_report(text)
            self._by_report[report_key(report)].append(case)
            self._by_report_loose[report_key(report, with_volume=False)].append(case)
        for name in (case.name, *case.aliases):
            self._by_name[normalize_case_name(name)].append(case)
        return case

    def _forget(self, case: CanonicalCase) -> None:
        if case.neutral:
            self._by_neutral.pop(case.neutral.text.lower(), None)
        for text in case.reports:
            report = parse_report(text)
            for bucket, key in (
                (self._by_report, report_key(report)),
                (self._by_report_loose, report_key(report, with_volume=False)),
            ):
                if case in bucket.get(key, []):
                    bucket[key].remove(case)
        for name in (case.name, *case.aliases):
            key = normalize_case_name(name)
            if case in self._by_name.get(key, []):
                self._by_name[key].remove(case)

    # -- lookup ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[CanonicalCase]:
        return iter(self._by_id.values())

    def get(self, case_id_: str) -> CanonicalCase | None:
        return self._by_id.get(case_id_)

    def by_neutral(self, neutral: NeutralCitation) -> CanonicalCase | None:
        return self._by_neutral.get(neutral.text.lower())

    def by_report(self, report: ReportCitation) -> tuple[list[CanonicalCase], bool]:
        """Cases reported at this citation, and whether the volume had to be ignored.

        "[2005] AC 134" and "[2005] 1 AC 134" are the same report cited with
        and without its volume, so a miss on the exact key falls back to a
        volume-free one -- but says so, because that is weaker evidence.
        """
        hits = list(self._by_report.get(report_key(report), ()))
        if hits:
            return hits, False
        return list(self._by_report_loose.get(report_key(report, with_volume=False), ())), True

    def by_name(self, name: str) -> list[CanonicalCase]:
        return list(self._by_name.get(normalize_case_name(name), ()))

    def name_keys(self) -> set[str]:
        """Normalised names known to the registry.

        Extraction uses this to accept a case cited by name alone, with no
        citation next to it -- "X v Y" in prose is only treated as a case
        reference when it is a case the registry knows.
        """
        return {key for key, cases in self._by_name.items() if cases}

    def search(
        self, name: str, limit: int = 5, cutoff: float = 0.55
    ) -> list[tuple[CanonicalCase, float]]:
        """Fuzzy name search, scored in [0, 1] and sorted best-first."""
        probe = normalize_case_name(name)
        if not probe:
            return []
        best: dict[str, tuple[CanonicalCase, float]] = {}
        for key, cases in self._by_name.items():
            score = similarity(probe, key)
            if score < cutoff:
                continue
            for case in cases:
                if case.id not in best or score > best[case.id][1]:
                    best[case.id] = (case, score)
        scored = sorted(best.values(), key=lambda s: (-s[1], s[0].name))
        return scored[:limit]


def _report_id(report_text: str) -> str:
    report = parse_report(report_text)
    return "report-" + report_key(report).replace("|", "-").replace("--", "-")
