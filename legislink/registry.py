"""The canonical work registry and its lookup indexes.

The registry is the authority a citation is resolved *against*. It ships with a
seed of frequently cited UK works, and is extensible: point
``LEGISLINK_REGISTRY`` at a JSON file, or call :meth:`Registry.add` /
:meth:`Registry.load_file` at runtime.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Iterator

from .models import CanonicalWork
from .normalize import normalize_alias, normalize_title, similarity, title_stem

DATA_DIR = Path(__file__).parent / "data"
SEED_FILE = DATA_DIR / "works.json"
ENV_VAR = "LEGISLINK_REGISTRY"

#: Multiplier applied to a fuzzy title score when the citation names a year
#: and the candidate is from a different one. Chosen to land a same-title,
#: wrong-year hit between Resolver.suggest and Resolver.accept.
CROSS_YEAR_PENALTY = 0.75


def make_id(work_type: str, year: int, number: str) -> str:
    return f"{work_type}-{year}-{number}"


class Registry:
    """An in-memory index of canonical works."""

    def __init__(self, works: Iterable[CanonicalWork] = ()) -> None:
        self._by_id: dict[str, CanonicalWork] = {}
        self._by_title_year: dict[tuple[str, int], list[CanonicalWork]] = defaultdict(list)
        self._by_title: dict[str, list[CanonicalWork]] = defaultdict(list)
        self._by_stem_year: dict[tuple[str, int], list[CanonicalWork]] = defaultdict(list)
        self._by_alias: dict[str, list[CanonicalWork]] = defaultdict(list)
        self._by_number: dict[tuple[int, str], list[CanonicalWork]] = defaultdict(list)
        self._by_year: dict[int, list[CanonicalWork]] = defaultdict(list)
        for work in works:
            self.add(work)

    # -- construction ------------------------------------------------------

    @classmethod
    def default(cls) -> "Registry":
        """Seed registry, plus anything named by ``LEGISLINK_REGISTRY``."""
        registry = cls.from_file(SEED_FILE)
        extra = os.environ.get(ENV_VAR)
        if extra:
            for part in extra.split(os.pathsep):
                if part.strip():
                    registry.load_file(Path(part.strip()))
        return registry

    @classmethod
    def from_file(cls, path: str | Path) -> "Registry":
        registry = cls()
        registry.load_file(path)
        return registry

    def load_file(self, path: str | Path) -> int:
        """Merge works from a JSON file. Returns the number added."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = payload["works"] if isinstance(payload, dict) else payload
        count = 0
        for entry in entries:
            self.add(self._coerce(entry))
            count += 1
        return count

    @staticmethod
    def _coerce(entry: dict) -> CanonicalWork:
        work_type = entry["work_type"]
        year = int(entry["year"])
        number = str(entry["number"])
        return CanonicalWork(
            id=entry.get("id") or make_id(work_type, year, number),
            title=entry["title"],
            year=year,
            work_type=work_type,
            number=number,
            aliases=tuple(entry.get("aliases", ())),
        )

    def add(self, work: CanonicalWork) -> CanonicalWork:
        """Add or merge a work. Re-adding an id merges its aliases."""
        existing = self._by_id.get(work.id)
        if existing:
            merged = tuple(dict.fromkeys(existing.aliases + work.aliases))
            work = replace(existing, aliases=merged)
            self._forget(existing)

        self._by_id[work.id] = work
        norm = normalize_title(work.title)
        self._by_title_year[(norm, work.year)].append(work)
        self._by_title[norm].append(work)
        self._by_stem_year[(title_stem(work.title), work.year)].append(work)
        self._by_number[(work.year, work.number)].append(work)
        self._by_year[work.year].append(work)
        for alias in work.aliases:
            self._by_alias[normalize_alias(alias)].append(work)
        return work

    def _forget(self, work: CanonicalWork) -> None:
        norm = normalize_title(work.title)
        for bucket, key in (
            (self._by_title_year, (norm, work.year)),
            (self._by_title, norm),
            (self._by_stem_year, (title_stem(work.title), work.year)),
            (self._by_number, (work.year, work.number)),
            (self._by_year, work.year),
        ):
            if work in bucket.get(key, []):
                bucket[key].remove(work)
        for alias in work.aliases:
            key = normalize_alias(alias)
            if work in self._by_alias.get(key, []):
                self._by_alias[key].remove(work)

    # -- lookup ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[CanonicalWork]:
        return iter(self._by_id.values())

    def get(self, work_id: str) -> CanonicalWork | None:
        return self._by_id.get(work_id)

    def by_number(
        self, year: int, number: str, work_type: str | None = None
    ) -> list[CanonicalWork]:
        """Look up by official number, e.g. year=1968, number="60"."""
        hits = list(self._by_number.get((year, str(number)), ()))
        if work_type:
            hits = [w for w in hits if w.work_type == work_type]
        return hits

    def by_title(self, title: str, year: int | None = None) -> list[CanonicalWork]:
        """Exact (normalised) title lookup, optionally pinned to a year."""
        norm = normalize_title(title)
        if year is not None:
            hits = list(self._by_title_year.get((norm, year), ()))
            if hits:
                return hits
            return list(self._by_stem_year.get((title_stem(title), year), ()))
        return list(self._by_title.get(norm, ()))

    def by_alias(self, alias: str) -> list[CanonicalWork]:
        return list(self._by_alias.get(normalize_alias(alias), ()))

    def alias_keys(self) -> set[str]:
        """Normalised aliases known to the registry, e.g. ``{"pace", "tupe"}``.

        Extraction uses this to accept an undefined acronym such as "under
        TUPE" -- a registered alias is evidence the acronym is legislation.
        """
        return {key for key, works in self._by_alias.items() if works}

    def by_year(self, year: int) -> list[CanonicalWork]:
        return list(self._by_year.get(year, ()))

    def search(
        self, title: str, year: int | None = None, limit: int = 5, cutoff: float = 0.55
    ) -> list[tuple[CanonicalWork, float]]:
        """Fuzzy title search, scored in [0, 1] and sorted best-first.

        When a year is given, works from that year are scored first. If none
        clears the cutoff, other years are searched but heavily penalised: in
        UK citation the year is part of the title, so "Companies Act 1985" and
        "Companies Act 2006" are different Acts, not a typo for one another.
        The penalty is calibrated to leave a cross-year hit above the default
        suggestion bar but below the default acceptance bar, so it surfaces as
        a candidate for a human rather than resolving silently.
        """
        probe = normalize_title(title)
        if not probe:
            return []

        pool = self._by_year.get(year, []) if year is not None else []
        scored = [(w, similarity(probe, normalize_title(w.title))) for w in pool]
        scored = [s for s in scored if s[1] >= cutoff]

        if not scored:
            penalty = 1.0 if year is None else CROSS_YEAR_PENALTY
            scored = [
                (w, similarity(probe, normalize_title(w.title)) * penalty)
                for w in self._by_id.values()
            ]
            scored = [s for s in scored if s[1] >= cutoff]

        # Ties are broken toward the most recent year: identically titled works
        # ("Data Protection Act 1998" / "2018") score the same, and the current
        # Act is nearly always the one being looked for.
        scored.sort(key=lambda s: (-s[1], -s[0].year, s[0].title))
        return scored[:limit]
