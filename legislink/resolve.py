"""Pass 2: resolve extracted references to canonical works and cases.

Resolution is ordered by how much evidence each strategy needs, strongest
first: an official number is decisive, an exact title is near-decisive, a
registered alias is reliable, and fuzzy title matching is the last resort and
is reported with its score so callers can set their own bar.

Anaphora ("the Act", "the 1998 Act", "the DPA") is resolved against the
document read left to right, which is how these references are meant to be
read -- a back-reference points at the most recent compatible antecedent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cases import CaseRegistry
from .courts import COURTS, case_id, judgment_url, search_url as case_search_url
from .extract import CaseMention, Mention, RawCitation, extract, find_cases
from .link import build as build_links
from .models import (
    CanonicalCase, CanonicalWork, CaseCitation, CaseRef, Citation, DocumentResult,
    Version, WorkRef,
)
from .normalize import normalize_alias, normalize_case_name, normalize_title, similarity
from .registry import Registry, make_id

#: Document types that count as primary legislation for "the Act".
ACT_TYPES = frozenset({"ukpga", "ukla", "asp", "nia", "anaw", "asc", "ukcm"})
#: ...and as secondary legislation for "the Regulations"/"the Order".
SI_TYPES = frozenset({"uksi", "ssi", "nisr", "wsi"})

_NOUN_TYPES = {
    "act": ACT_TYPES,
    "measure": ACT_TYPES,
    "regulations": SI_TYPES,
    "order": SI_TYPES,
    "rules": SI_TYPES,
}


def infer_work_type(title: str | None, year: int | None) -> str:
    """Best guess at the document type from the title alone.

    Used only when the citation carries no official number, so a wrong guess
    degrades to a search link rather than a wrong direct link.
    """
    if not title:
        return "ukpga"
    lowered = title.lower()
    secondary = lowered.rstrip(".").split()[-1] in (
        "regulations", "rules", "order", "orders", "scheme", "regulation"
    )
    y = year or 0

    if "(scotland)" in lowered:
        if secondary:
            return "ssi"
        return "asp" if y >= 1999 else "ukpga"
    if "(northern ireland)" in lowered:
        if secondary:
            return "nisr"
        return "nia" if y >= 2000 else "ukpga"
    if "(wales)" in lowered or "(cymru)" in lowered:
        if secondary:
            return "wsi"
        if y >= 2020:
            return "asc"
        return "anaw" if y >= 2012 else "ukpga"
    if "measure" in lowered:
        return "ukcm"
    return "uksi" if secondary else "ukpga"


@dataclass
class Context:
    """Running state used to resolve back-references within one document."""

    short_forms: dict[str, CanonicalWork] = field(default_factory=dict)
    by_year: dict[int, CanonicalWork] = field(default_factory=dict)
    last: CanonicalWork | None = None
    last_act: CanonicalWork | None = None
    last_si: CanonicalWork | None = None

    def remember(self, work: CanonicalWork) -> None:
        self.last = work
        self.by_year[work.year] = work
        if work.work_type in ACT_TYPES:
            self.last_act = work
        elif work.work_type in SI_TYPES:
            self.last_si = work


@dataclass
class CaseMatch:
    case: CanonicalCase | None
    confidence: float
    method: str
    candidates: tuple[tuple[str, float], ...] = ()
    notes: tuple[str, ...] = ()


#: Below this, a cited name and a registry entry are treated as different
#: cases. Deliberately low: reports abbreviate party names heavily ("R
#: (UNISON) v Lord Chancellor" is listed as "R v Lord Chancellor").
CASE_NAME_AGREES = 0.6


@dataclass
class Match:
    work: CanonicalWork | None
    confidence: float
    method: str
    candidates: tuple[tuple[str, float], ...] = ()
    notes: tuple[str, ...] = ()


class Resolver:
    """Resolves extracted references against a :class:`Registry`.

    ``accept`` is the fuzzy score at or above which a match is returned as
    resolved; ``suggest`` is the lower bar at or above which a near-miss is
    still reported in ``candidates`` but left unresolved.

    Case citations are resolved against ``case_registry`` (the seed case
    registry by default). Pass ``link_cases=False`` to skip case law
    entirely, which restores the legislation-only behaviour.
    """

    def __init__(
        self,
        registry: Registry | None = None,
        *,
        accept: float = 0.86,
        suggest: float = 0.60,
        remote=None,
        default_version: Version | None = None,
        case_registry: CaseRegistry | None = None,
        link_cases: bool = True,
    ) -> None:
        self.registry = registry if registry is not None else Registry.default()
        self.accept = accept
        self.suggest = suggest
        self.remote = remote
        self.default_version = default_version or Version()
        self.link_cases = link_cases
        self.cases = (
            case_registry if case_registry is not None
            else CaseRegistry.default() if link_cases else CaseRegistry()
        )

    # -- entry points ------------------------------------------------------

    def resolve_document(self, text: str, version: Version | None = None) -> DocumentResult:
        """Extract and resolve every citation in ``text``.

        Case citations are found first, and their spans are withheld from
        legislation extraction: "[1996] Ch 1" is a law report, not Chapter 1.
        """
        cases: list[CaseCitation] = []
        if self.link_cases:
            mentions = find_cases(text, self.cases.name_keys())
            cases = [self._resolve_case(text, m) for m in mentions]

        raw_citations, definitions = extract(
            text, self.registry.alias_keys(), exclude=[(c.start, c.end) for c in cases]
        )
        context = Context()
        self._seed_short_forms(definitions, context)
        effective_version = version or self.default_version

        citations: list[Citation] = []
        for raw in raw_citations:
            citations.append(self._resolve_citation(text, raw, context, effective_version))
        return DocumentResult(text=text, citations=citations, cases=cases)

    def resolve_text(self, text: str, version: Version | None = None) -> DocumentResult:
        """Alias for :meth:`resolve_document`, for short fragments."""
        return self.resolve_document(text, version=version)

    def resolve_work(
        self, title: str, year: int | None = None, number: str | None = None,
        version: Version | None = None,
    ) -> Match:
        """Resolve a single work reference outside any document context."""
        _ = version
        return self._match(
            WorkRef(surface=title, title=title, year=year, number=number), Context()
        )

    def resolve_case(self, ref: CaseRef) -> CaseMatch:
        """Resolve a single case reference outside any document."""
        return self._match_case(ref)

    # -- internals ---------------------------------------------------------

    def _seed_short_forms(self, definitions: dict[str, Mention], context: Context) -> None:
        """Bind short forms defined in the document before the first pass.

        A judgment defines "the DPA" once and uses it throughout, including in
        sentences that precede nothing -- binding up front means a definition
        works for the whole document, not just what follows it.
        """
        for short, mention in definitions.items():
            match = self._match(mention.ref, context)
            if match.work:
                context.short_forms[normalize_alias(short)] = match.work

    def _resolve_citation(
        self,
        text: str,
        raw: RawCitation,
        context: Context,
        version: Version | None = None,
    ) -> Citation:
        chain = raw.provisions
        surface = text[raw.start: raw.end]
        effective_version = version or self.default_version

        if raw.mention is None:
            # A bare pinpoint: "section 45" with no work in sight.
            work = context.last
            if work:
                match = Match(work, 0.70, "context", notes=("pinpoint attached to most recent work",))
            else:
                match = Match(None, 0.0, "unresolved", notes=("pinpoint with no antecedent work",))
            ref = WorkRef(surface="", anaphoric=True)
        else:
            ref = raw.mention.ref
            match = self._match(ref, context)

        if match.work:
            context.remember(match.work)

        url, id_uri, all_urls = build_links(
            match.work,
            chain,
            title=ref.title,
            year=ref.year,
            version=effective_version,
        )
        notes = list(match.notes)
        if len(all_urls) > 1:
            notes.append(f"{len(all_urls)} provisions cited; url is the first")
        if any(p.end_number for p in chain):
            notes.append("range cited; url targets the first provision")

        return Citation(
            surface=surface,
            start=raw.start,
            end=raw.end,
            work_ref=ref,
            provisions=chain,
            work=match.work,
            confidence=match.confidence,
            method=match.method,
            candidates=match.candidates,
            notes=tuple(notes),
            url=url,
            id_uri=id_uri,
            version=effective_version,
        )

    def _match(self, ref: WorkRef, context: Context) -> Match:
        if ref.anaphoric:
            return self._match_anaphor(ref, context)

        # 1. An official number is decisive.
        if ref.number and ref.year:
            work_type = ref.work_type or infer_work_type(ref.title, ref.year)
            hits = self.registry.by_number(ref.year, ref.number, work_type)
            if not hits:
                hits = self.registry.by_number(ref.year, ref.number)
            if len(hits) == 1:
                return Match(hits[0], 1.0, "number")
            if len(hits) > 1 and ref.title:
                best = max(hits, key=lambda w: self._title_score(ref.title, w))
                return Match(best, 0.97, "number", notes=("several works share that number",))
            # Not in the registry, but the number tells us the URI exactly.
            synthetic = CanonicalWork(
                id=make_id(work_type, ref.year, ref.number),
                title=ref.title or f"{work_type} {ref.year}/{ref.number}",
                year=ref.year,
                work_type=work_type,
                number=ref.number,
            )
            return Match(synthetic, 0.95, "number", notes=("constructed from the cited number; not in registry",))

        # 2. Exact normalised title.
        if ref.title:
            hits = self.registry.by_title(ref.title, ref.year)
            if len(hits) == 1:
                return Match(hits[0], 1.0, "exact")
            if len(hits) > 1:
                return Match(hits[0], 0.90, "exact", notes=("ambiguous title; first match used",))

            # 3. A registered alias.
            alias_hits = self.registry.by_alias(ref.title)
            if len(alias_hits) == 1:
                return Match(alias_hits[0], 0.95, "alias")
            if len(alias_hits) > 1 and ref.year:
                same_year = [w for w in alias_hits if w.year == ref.year]
                if len(same_year) == 1:
                    return Match(same_year[0], 0.95, "alias")

            # 4. Fuzzy.
            scored = self.registry.search(ref.title, ref.year, cutoff=self.suggest)
            candidates = tuple((w.id, s) for w, s in scored)
            if scored and scored[0][1] >= self.accept:
                return Match(scored[0][0], scored[0][1], "fuzzy", candidates)

            # 5. Optional remote lookup against legislation.gov.uk.
            if self.remote is not None:
                found = self.remote.find(ref.title, ref.year)
                if found:
                    self.registry.add(found)
                    return Match(found, 0.93, "remote", candidates)

            return Match(None, 0.0, "unresolved", candidates,
                         ("no registry entry above the acceptance threshold",))

        return Match(None, 0.0, "unresolved", notes=("no title or number to resolve",))

    def _match_anaphor(self, ref: WorkRef, context: Context) -> Match:
        surface = ref.surface.lower()

        # "the DPA 2018" / "the CPR"
        if ref.title:
            key = normalize_alias(ref.title)
            work = context.short_forms.get(key)
            if work:
                return Match(work, 0.92, "short-form")
            hits = self.registry.by_alias(ref.title)
            if len(hits) == 1:
                return Match(hits[0], 0.88, "alias")
            if len(hits) > 1:
                return Match(hits[0], 0.70, "alias", notes=("ambiguous acronym; first match used",))

        # "the 1998 Act"
        if ref.year is not None:
            work = context.by_year.get(ref.year)
            if work:
                return Match(work, 0.90, "anaphora")
            hits = self.registry.by_year(ref.year)
            if len(hits) == 1:
                return Match(hits[0], 0.65, "anaphora", notes=("year matched the registry, not the document",))
            return Match(None, 0.0, "unresolved",
                         tuple((w.id, 0.5) for w in hits[:5]),
                         (f"no work from {ref.year} mentioned earlier",))

        # "the Act" / "the Regulations"
        for noun, types in _NOUN_TYPES.items():
            if noun in surface:
                work = context.last_act if types is ACT_TYPES else context.last_si
                if work:
                    return Match(work, 0.80, "anaphora")
                break

        if context.last:
            return Match(context.last, 0.60, "anaphora", notes=("fell back to the most recent work",))
        return Match(None, 0.0, "unresolved", notes=("back-reference with no antecedent",))

    def _title_score(self, title: str, work: CanonicalWork) -> float:
        return similarity(normalize_title(title), normalize_title(work.title))

    # -- case law ----------------------------------------------------------

    def _resolve_case(self, text: str, mention: CaseMention) -> CaseCitation:
        ref = mention.ref
        match = self._match_case(ref)
        notes = list(match.notes)

        url, source = None, None
        neutral = match.case.neutral if match.case else None
        if neutral is not None:
            url, source = judgment_url(neutral)
            if url is None:
                court = f"{neutral.court} ({neutral.division})" if neutral.division else neutral.court
                notes.append(f"no direct link for {court}")
                court_info = COURTS.get(neutral.court)
                if not (court_info and court_info.tna):
                    # Find Case Law does not hold this court; a search there
                    # would be a link that cannot succeed.
                    return self._case_citation(text, mention, match, notes, None, None)
        if url is None:
            query = (
                match.case.neutral.text if match.case and match.case.neutral
                else ref.name or (match.case.name if match.case else None)
                or (ref.reports[0].text if ref.reports else ref.surface)
            )
            url, source = case_search_url(query), "search"

        return self._case_citation(text, mention, match, notes, url, source)

    @staticmethod
    def _case_citation(
        text: str, mention: CaseMention, match: CaseMatch, notes: list[str],
        url: str | None, source: str | None,
    ) -> CaseCitation:
        return CaseCitation(
            surface=text[mention.start: mention.end],
            start=mention.start,
            end=mention.end,
            case_ref=mention.ref,
            case=match.case,
            confidence=match.confidence,
            method=match.method,
            candidates=match.candidates,
            notes=tuple(notes),
            url=url,
            source=source,
        )

    def _match_case(self, ref: CaseRef) -> CaseMatch:
        # 1. A neutral citation is decisive, registered or not.
        if ref.neutral:
            case = self.cases.by_neutral(ref.neutral)
            if case:
                notes = ()
                if ref.name and self._case_name_score(ref.name, case) < CASE_NAME_AGREES:
                    notes = (f"cited name differs from the registry entry ({case.name})",)
                return CaseMatch(case, 1.0, "neutral", notes=notes)
            synthetic = CanonicalCase(
                id=case_id(ref.neutral),
                name=ref.name or ref.neutral.text,
                neutral=ref.neutral,
                reports=tuple(r.text for r in ref.reports),
            )
            return CaseMatch(synthetic, 0.95, "neutral",
                             notes=("constructed from the neutral citation; not in registry",))

        # 2. A law report citation, cross-checked against the cited name.
        #    Report citations are not live-verifiable, and a mistyped page
        #    number points at a different case, so a name that disagrees
        #    blocks the match rather than being overridden by it.
        candidates: list[tuple[str, float]] = []
        for report in ref.reports:
            hits, loose = self.cases.by_report(report)
            if len(hits) != 1:
                candidates += [(c.id, 0.5) for c in hits]
                continue
            case = hits[0]
            notes = ("matched ignoring the report volume",) if loose else ()
            if ref.name is None:
                return CaseMatch(case, 0.85 if not loose else 0.80, "report",
                                 notes=notes + ("matched on the report citation alone",))
            score = self._case_name_score(ref.name, case)
            if score >= CASE_NAME_AGREES:
                return CaseMatch(case, 0.95 if not loose else 0.90, "report", notes=notes)
            return CaseMatch(None, 0.0, "unresolved", ((case.id, round(score, 4)),),
                             (f"{report.text} is registered to {case.name}, "
                              "but the cited name does not match",))

        # 3. The name alone -- exact, then fuzzy. A name is weak evidence when
        #    the text also cites a report the registry does not know ("R v
        #    Brown" names dozens of cases), so the years must agree.
        if ref.name:
            years = {r.year for r in ref.reports}
            hits = [c for c in self.cases.by_name(ref.name) if self._years_agree(c, years)]
            if len(hits) == 1:
                return CaseMatch(hits[0], 0.90, "name")
            if len(hits) > 1:
                return CaseMatch(None, 0.0, "unresolved", tuple((c.id, 1.0) for c in hits),
                                 ("several registered cases share that name",))

            scored = [
                (c, s) for c, s in self.cases.search(ref.name, cutoff=self.suggest)
                if self._years_agree(c, years)
            ]
            candidates += [(c.id, s) for c, s in scored]
            if scored and scored[0][1] >= self.accept:
                return CaseMatch(scored[0][0], scored[0][1], "fuzzy", tuple(candidates))

        return CaseMatch(None, 0.0, "unresolved", tuple(candidates),
                         ("no neutral citation, and no registry entry matched",))

    @staticmethod
    def _years_agree(case: CanonicalCase, years: set[int]) -> bool:
        """Could a report from one of ``years`` be a report of ``case``?

        Reports often appear a year after judgment ("[2019] UKSC 41" is
        reported at "[2020] AC 373"), so a one-year lag is allowed.
        """
        if not years or case.year is None:
            return True
        return any(case.year <= y <= case.year + 1 for y in years)

    @staticmethod
    def _case_name_score(name: str, case: CanonicalCase) -> float:
        """How well a cited name agrees with a registered case, in [0, 1]."""
        probe = normalize_case_name(name)
        return max(_name_agreement(probe, normalize_case_name(n)) for n in (case.name, *case.aliases))


def _name_agreement(a: str, b: str) -> float:
    """Compare two normalised case names party by party.

    Whole-string similarity is too forgiving here: "lachaux v smith" and
    "lachaux v independent print" share a claimant and score well, yet name
    different cases. Each side must agree on its own, and the weaker side
    decides. A side agrees fully when one is a subset of the other, which is
    how reports abbreviate names ("r v lord chancellor" for "r unison v lord
    chancellor").
    """
    sides_a, sides_b = a.split(" v "), b.split(" v ")
    if len(sides_a) != 2 or len(sides_b) != 2:
        return similarity(a, b)
    return min(_party_agreement(x, y) for x, y in zip(sides_a, sides_b))


def _party_agreement(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if ta and tb and (ta <= tb or tb <= ta):
        return 1.0
    return similarity(a, b)
