"""Institution data for the mock core: load an AICTE approved-institutions CSV, match spoken names.

The voice agent can only answer for colleges the core knows. This module lets the stand-in core
know every college in a CSV exported from AICTE (`data/aicte_institutions.csv`). Without that file
it serves two fictional demo colleges and never invents approval data for a real one.

Column names are matched loosely (see `_ALIASES`), so an export can be used as it is. Either layout works:

* one row per course (institute, year, course, intake) - rows of one institute are grouped; or
* one row per institute (no course column) - the institute is approved, with no course list.
"""

from __future__ import annotations

import csv
import difflib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# header (normalised: lowercase, non-alphanumerics -> "_") -> our field
_ALIASES: dict[str, tuple[str, ...]] = {
    "name": (
        "name",
        "institute_name",
        "institution_name",
        "institution",
        "institute",
        "college",
        "college_name",
        "name_of_the_institute",
        "name_of_institution",
    ),
    "id": ("id", "institution_id", "institute_id", "aicte_id", "permanent_id", "application_id"),
    "state": ("state", "state_name", "state_ut"),
    "city": ("city", "district", "district_name", "town", "location", "place"),
    "year": ("academic_year", "year", "ay", "session", "approval_year"),
    "course": (
        "course",
        "course_name",
        "programme",
        "program",
        "branch",
        "programme_name",
        "program_name",
        "discipline",
    ),
    "intake": (
        "intake",
        "approved_intake",
        "sanctioned_intake",
        "total_intake",
        "seats",
        "intake_approved",
    ),
    "approved": ("approved", "approval_status", "status", "is_approved"),
    "aliases": ("aliases", "alias", "short_name", "also_known_as", "acronym"),
}
_YES = {"y", "yes", "true", "1", "approved", "active", "open", "approved_for_the_year"}
# words that say little about WHICH college it is
_COMMON = {
    "college",
    "colleges",
    "institute",
    "institutes",
    "institution",
    "university",
    "school",
    "of",
    "the",
    "and",
    "for",
    "in",
    "at",
    "engineering",
    "technology",
    "technical",
    "sciences",
    "science",
    "management",
    "studies",
    "education",
    "educational",
    "polytechnic",
    "autonomous",
}
_ACRONYM_SKIP = {"of", "the", "and", "for", "in", "at"}
THRESHOLD = 0.6
MAX_RESULTS = 5


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower().replace("&", " and ")).strip()


def _tokens(text: str) -> list[str]:
    return _norm(text).split()


def _slug(text: str) -> str:
    return "-".join(_tokens(text))[:80]


@dataclass
class Institution:
    id: str
    name: str
    city: str = ""
    state: str = ""
    aliases: list[str] = field(default_factory=list)
    years: dict[str, dict[str, Any]] = field(default_factory=dict)  # year -> {approved, courses}

    @property
    def public(self) -> dict[str, str]:
        out = {"id": self.id, "name": self.name}
        if self.city:
            out["city"] = self.city
        if self.state:
            out["state"] = self.state
        return out

    @property
    def acronym(self) -> str:
        return "".join(t[0] for t in _tokens(self.name) if t not in _ACRONYM_SKIP)


class Directory:
    """Every known institution plus an inverted token index for fast fuzzy lookup."""

    def __init__(self, institutions: list[Institution]) -> None:
        self.institutions = institutions
        self.by_id = {i.id: i for i in institutions}
        self._name_index: dict[str, set[int]] = defaultdict(set)  # name token -> institutions
        self._place_index: dict[str, set[int]] = defaultdict(
            set
        )  # city/state token -> institutions
        self._acronyms: dict[str, set[int]] = defaultdict(set)
        for n, inst in enumerate(institutions):
            for tok in _tokens(inst.name):
                self._name_index[tok].add(n)
            for alias in inst.aliases:
                for tok in _tokens(alias):
                    self._name_index[tok].add(n)
                self._acronyms["".join(_tokens(alias))].add(n)
            for tok in _tokens(f"{inst.city} {inst.state}"):
                self._place_index[tok].add(n)
            if len(inst.acronym) >= 2:
                self._acronyms[inst.acronym].add(n)

    def __len__(self) -> int:
        return len(self.institutions)

    # -- matching --------------------------------------------------------------------------
    def _similar(self, token: str, index: dict[str, set[int]]) -> set[int]:
        """Institutions having a token equal or (for longer words) close to `token`."""
        found = set(index.get(token, ()))
        if len(token) >= 4:
            for close in difflib.get_close_matches(token, index.keys(), n=6, cutoff=0.8):
                found |= index[close]
        return found

    def search(self, query: str) -> list[Institution]:
        q = _norm(query)
        if not q:
            return []
        scores: dict[int, float] = defaultdict(float)
        words = [t for t in q.split() if t not in _ACRONYM_SKIP]
        distinctive = [t for t in words if t not in _COMMON]

        # 1. the whole phrase appears in the name ("sri" -> every Sri ... college). A query of only
        #    generic words ("college", "engineering college") names no particular college.
        for n, inst in enumerate(self.institutions if distinctive else ()):
            hay = _norm(inst.name)
            if q in hay:
                scores[n] = max(scores[n], 0.7 + 0.3 * len(q) / len(hay))
        # 2. an acronym ("MGIT", "M G I T")
        for n in self._acronyms.get(q.replace(" ", ""), ()):
            scores[n] = max(scores[n], 0.95)
        # 3. the distinctive words, allowing recognition errors and a city/state to narrow it
        if distinctive:
            hits: dict[int, int] = defaultdict(int)
            for tok in distinctive:
                matched = self._similar(tok, self._name_index) | self._similar(
                    tok, self._place_index
                )
                for n in matched:
                    hits[n] += 1
            for n, count in hits.items():
                coverage = count / len(distinctive)
                if coverage >= 0.66:
                    bonus = 0.05 if count == len(distinctive) else 0.0
                    scores[n] = max(scores[n], 0.6 + 0.3 * coverage + bonus)
        # 4. a generally close spelling of the whole name
        if len(q) >= 6:
            names = {_norm(i.name): n for n, i in enumerate(self.institutions)}
            for close in difflib.get_close_matches(q, names.keys(), n=3, cutoff=0.78):
                scores[names[close]] = max(
                    scores[names[close]], difflib.SequenceMatcher(None, q, close).ratio()
                )

        ranked = sorted(
            ((s, n) for n, s in scores.items() if s >= THRESHOLD), key=lambda t: (-t[0], t[1])
        )
        if not ranked:
            return []
        top = ranked[0][0]
        if top >= 0.95 and (len(ranked) == 1 or ranked[1][0] < top - 0.1):
            return [self.institutions[ranked[0][1]]]
        # keep only the ones nearly as good as the best: a clear leader must not be buried
        close = [r for r in ranked if r[0] >= top - 0.2]
        return [self.institutions[n] for _, n in close[:MAX_RESULTS]]

    # -- approval --------------------------------------------------------------------------
    def approval(self, institution_id: str, academic_year: str | None) -> dict[str, Any] | None:
        inst = self.by_id.get(institution_id)
        if inst is None:
            return None
        if not inst.years:
            return None
        year = academic_year if academic_year in inst.years else max(inst.years)
        rec = inst.years[year]
        return {
            "institution_id": inst.id,
            "academic_year": year,
            "approved": bool(rec["approved"]),
            "courses": rec["courses"],
        }


# -- loading ---------------------------------------------------------------------------------
def _pick(row: dict[str, str], key: str) -> str:
    for alias in _ALIASES[key]:
        value = row.get(alias)
        if value is not None and value.strip():
            return value.strip()
    return ""


def _as_int(text: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else None


def load_csv(path: str | Path) -> Directory:
    """Read an AICTE-style export. Raises ValueError if no institute-name column is found."""
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"{path} is empty")
        rows = [
            {
                re.sub(r"[^a-z0-9]+", "_", (k or "").lower()).strip("_"): (v or "")
                for k, v in r.items()
            }
            for r in reader
        ]
    if not any(_pick(r, "name") for r in rows):
        raise ValueError(
            f"{path}: no institute-name column. Use one of: {', '.join(_ALIASES['name'])}"
        )

    grouped: dict[str, Institution] = {}
    for row in rows:
        name = _pick(row, "name")
        if not name:
            continue
        city, state = _pick(row, "city"), _pick(row, "state")
        iid = _pick(row, "id") or _slug(f"{name} {city}")
        inst = grouped.get(iid)
        if inst is None:
            inst = grouped[iid] = Institution(id=iid, name=name, city=city, state=state)
        for alias in re.split(r"[;|]", _pick(row, "aliases")):
            if alias.strip() and alias.strip() not in inst.aliases:
                inst.aliases.append(alias.strip())
        approved_text = _pick(row, "approved")
        approved = True if not approved_text else _norm(approved_text).replace(" ", "_") in _YES
        year = _pick(row, "year") or "current year"
        rec = inst.years.setdefault(year, {"approved": False, "courses": []})
        rec["approved"] = rec["approved"] or approved
        course = _pick(row, "course")
        if course and approved:
            rec["courses"].append({"name": course, "intake": _as_int(_pick(row, "intake")) or 0})
    return Directory(list(grouped.values()))
