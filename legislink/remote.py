"""Optional lookups against the live legislation.gov.uk service.

Everything here is opt-in. The resolver only calls it when a citation cannot
be matched locally, and any hit is folded back into the in-memory registry so
the same title is resolved offline for the rest of the run.

Requires the ``remote`` extra (``pip install legislink[remote]``).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .models import BASE, CanonicalWork
from .normalize import normalize_title, similarity
from .registry import make_id

ATOM = "{http://www.w3.org/2005/Atom}"

# legislation.gov.uk identifiers look like http://www.legislation.gov.uk/id/ukpga/1998/42
_ID_RE = re.compile(r"/id/(?P<type>[a-z]+)/(?P<year>\d{4})/(?P<number>[^/\s]+)")


class LegislationGovUk:
    """A thin, cached client over the Atom search feed."""

    def __init__(self, timeout: float = 10.0, min_score: float = 0.8, client=None) -> None:
        self.timeout = timeout
        self.min_score = min_score
        self._client = client
        self._cache: dict[tuple[str, int | None], CanonicalWork | None] = {}

    def _http(self):
        if self._client is None:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "remote lookups need the 'remote' extra: pip install legislink[remote]"
                ) from exc
            self._client = httpx.Client(
                timeout=self.timeout, headers={"Accept": "application/atom+xml"}
            )
        return self._client

    def find(self, title: str, year: int | None = None) -> CanonicalWork | None:
        """Search for a work by title, returning the best confident match."""
        key = (normalize_title(title), year)
        if key in self._cache:
            return self._cache[key]

        params = {"title": title}
        if year:
            params["year"] = str(year)
        try:
            response = self._http().get(f"{BASE}/all/data.feed", params=params)
            response.raise_for_status()
            work = self._best(response.text, title, year)
        except Exception:
            work = None  # a network problem must not fail resolution
        self._cache[key] = work
        return work

    def _best(self, feed: str, title: str, year: int | None) -> CanonicalWork | None:
        probe = normalize_title(title)
        best: tuple[CanonicalWork, float] | None = None

        for entry in ET.fromstring(feed).iter(f"{ATOM}entry"):
            id_el = entry.find(f"{ATOM}id")
            title_el = entry.find(f"{ATOM}title")
            if id_el is None or id_el.text is None or title_el is None:
                continue
            m = _ID_RE.search(id_el.text)
            if not m:
                continue
            entry_title = (title_el.text or "").strip()
            score = similarity(probe, normalize_title(entry_title))
            entry_year = int(m.group("year"))
            if year is not None and entry_year != year:
                score *= 0.8
            if score >= self.min_score and (best is None or score > best[1]):
                work = CanonicalWork(
                    id=make_id(m.group("type"), entry_year, m.group("number")),
                    title=entry_title,
                    year=entry_year,
                    work_type=m.group("type"),
                    number=m.group("number"),
                )
                best = (work, score)

        return best[0] if best else None

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()
