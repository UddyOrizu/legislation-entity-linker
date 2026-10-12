"""Turn a neutral citation into a link to the judgment.

A neutral citation identifies a judgment exactly, so -- like an official
chapter number for legislation -- the link can be built without any lookup.

Two services are used:

Find Case Law (The National Archives)
    The primary target: ``https://caselaw.nationalarchives.gov.uk/uksc/2019/41``.
    Every path shape in :data:`COURTS` with a ``tna`` entry was checked
    against the live service. Its coverage is not complete, so a correctly
    built link can still 404 for an individual judgment.
BAILII
    Used only where Find Case Law has nothing: the House of Lords, Scotland,
    the Northern Ireland Court of Appeal, the old EAT numbering, and the
    retrospective neutral citations BAILII assigned to pre-2001 judgments.
    BAILII refuses automated requests, so these shapes are *not*
    live-verified.

Courts recognised but given no template (e.g. ``NIQB``) are resolved without
a direct link rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus

from .models import NeutralCitation

TNA = "https://caselaw.nationalarchives.gov.uk"
BAILII = "https://www.bailii.org"

#: Neutral citations were introduced in 2001; anything earlier is a BAILII
#: retrospective citation, which Find Case Law does not hold.
NEUTRAL_SINCE = 2001


@dataclass(frozen=True)
class Court:
    code: str
    name: str
    tna: str | None = None
    bailii: str | None = None
    #: Divisions with a Find Case Law path, keyed by canonical spelling.
    divisions: tuple[str, ...] = ()
    #: True when the division must be cited for the path to exist (EWHC);
    #: False when it is an optional tier (EWFC "(B)", EWCOP "(T2)").
    division_required: bool = False


COURTS: dict[str, Court] = {c.code: c for c in (
    Court("UKSC", "UK Supreme Court", tna="uksc",
          bailii="uk/cases/UKSC/{year}/{number}.html"),
    Court("UKPC", "Privy Council", tna="ukpc",
          bailii="uk/cases/UKPC/{year}/{number}.html"),
    Court("UKHL", "House of Lords", bailii="uk/cases/UKHL/{year}/{number}.html"),
    Court("EWCA Civ", "Court of Appeal (Civil Division)", tna="ewca/civ",
          bailii="ew/cases/EWCA/Civ/{year}/{number}.html"),
    Court("EWCA Crim", "Court of Appeal (Criminal Division)", tna="ewca/crim",
          bailii="ew/cases/EWCA/Crim/{year}/{number}.html"),
    Court("EWHC", "High Court", tna="ewhc", division_required=True, divisions=(
        "Admin", "Ch", "QB", "KB", "Fam", "Comm", "TCC", "Pat", "IPEC",
        "Admlty", "SCCO", "Mercantile",
    )),
    Court("EWFC", "Family Court", tna="ewfc", divisions=("B",)),
    Court("EWCOP", "Court of Protection", tna="ewcop", divisions=("T1", "T2", "T3")),
    Court("EWCC", "County Court", tna="ewcc"),
    Court("EWCR", "Crown Court", tna="ewcr"),
    Court("UKUT", "Upper Tribunal", tna="ukut", division_required=True,
          divisions=("IAC", "LC", "AAC", "TCC")),
    Court("UKFTT", "First-tier Tribunal", tna="ukftt", division_required=True,
          divisions=("TC", "GRC")),
    Court("EAT", "Employment Appeal Tribunal", tna="eat"),
    Court("UKEAT", "Employment Appeal Tribunal",
          bailii="uk/cases/UKEAT/{year}/{number}.html"),
    Court("UKEATS", "Employment Appeal Tribunal (Scotland)"),
    Court("UKIPTrib", "Investigatory Powers Tribunal", tna="ukiptrib"),
    Court("CSIH", "Court of Session (Inner House)",
          bailii="scot/cases/ScotCS/{year}/{year}_CSIH_{number}.html"),
    Court("CSOH", "Court of Session (Outer House)",
          bailii="scot/cases/ScotCS/{year}/{year}_CSOH_{number}.html"),
    Court("HCJAC", "High Court of Justiciary (Appeal)",
          bailii="scot/cases/ScotHC/{year}/{year}_HCJAC_{number}.html"),
    Court("NICA", "Court of Appeal in Northern Ireland",
          bailii="nie/cases/NICA/{year}/{number}.html"),
    Court("NIQB", "High Court of Northern Ireland (QB)"),
    Court("NIKB", "High Court of Northern Ireland (KB)"),
    Court("NICh", "High Court of Northern Ireland (Ch)"),
    Court("NIFam", "High Court of Northern Ireland (Fam)"),
)}

# Accept any capitalisation of a division ("(ch)", "(ADMIN)") and the long
# form "(Patents)", but always emit the canonical spelling.
_DIVISION_SPELLINGS = {
    d.lower(): d for court in COURTS.values() for d in court.divisions
}
_DIVISION_SPELLINGS["patents"] = "Pat"


def canonical_court(raw: str) -> str:
    """``"EWCA  civ"`` -> ``"EWCA Civ"``; unknown codes are returned tidied."""
    tidy = " ".join(raw.split())
    for code in COURTS:
        if code.lower() == tidy.lower():
            return code
    return tidy


def canonical_division(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _DIVISION_SPELLINGS.get(raw.lower(), raw)


def make_neutral(year: int, court: str, number: str, division: str | None) -> NeutralCitation:
    return NeutralCitation(
        year=int(year),
        court=canonical_court(court),
        number=number,
        division=canonical_division(division),
    )


def case_id(neutral: NeutralCitation) -> str:
    """Stable id, e.g. ``uksc-2019-41`` or ``ewhc-ch-2019-123``."""
    parts = [neutral.court.lower().replace(" ", "-")]
    if neutral.division:
        parts.append(neutral.division.lower())
    parts += [str(neutral.year), neutral.number.lower()]
    return "-".join(parts)


def judgment_url(neutral: NeutralCitation) -> tuple[str | None, str | None]:
    """Return ``(url, source)`` for a neutral citation.

    ``source`` is ``"tna"`` or ``"bailii"``; both are ``None`` when the court
    is recognised but no reliable URL shape is known for it.
    """
    court = COURTS.get(neutral.court)
    if court is None:
        return None, None

    if court.tna and neutral.year >= NEUTRAL_SINCE:
        path = _tna_path(court, neutral)
        if path:
            return f"{TNA}/{path}/{neutral.year}/{neutral.number}", "tna"

    if court.bailii and not (court.division_required or neutral.division):
        path = court.bailii.format(year=neutral.year, number=neutral.number)
        return f"{BAILII}/{path}", "bailii"

    return None, None


def _tna_path(court: Court, neutral: NeutralCitation) -> str | None:
    division = neutral.division
    if division is None:
        return None if court.division_required else court.tna
    if division not in court.divisions:
        return None  # e.g. "(Costs)": cited, but no Find Case Law path
    return f"{court.tna}/{division.lower()}"


def search_url(query: str) -> str:
    """Fallback link into Find Case Law search for an unresolved case."""
    return f"{TNA}/search?query={quote_plus(query)}"
