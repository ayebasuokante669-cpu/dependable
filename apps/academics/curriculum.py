"""The class ladder and subject sets used to seed a school.

This is the single source for seeding. Edit the tuples below and re-run
``manage.py seed_academics --replace-subjects``; nothing else needs touching.

A subject is listed once and attached to every class it is taught in. That is
the point of the many-to-many: "Religion Studies" runs from Pre-KG to JSS 3 as
one row, not sixteen near-duplicates, and "Mathematics" is one row reaching both
senior arms rather than one per arm. Where a name differs between levels it is a
different subject -- "Social Studies" (primary) and "Social & Citizenship
Studies" (secondary) are separate rows, as are "History" and "Nigeria History",
and as are "Home Economics" (primary/junior) and "Home Management" (SSS Arts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Level

NURSERY = Level.NURSERY
PRIMARY = Level.PRIMARY
JUNIOR = Level.JUNIOR_SECONDARY
SENIOR = Level.SENIOR_SECONDARY


@dataclass(frozen=True)
class ClassSpec:
    """One rung of the admission ladder, optionally split into arms."""

    name: str
    level: int
    year_in_level: int
    #: Empty means a single group; otherwise one class per arm.
    streams: tuple[str, ...] = ()


@dataclass(frozen=True)
class Placement:
    """"This subject applies at this level" -- optionally only to some arms."""

    level: int
    streams: tuple[str, ...] = ()

    def matches(self, klass) -> bool:
        if klass.level != self.level:
            return False
        return not self.streams or klass.stream in self.streams


def at(level: int, *streams: str) -> Placement:
    """Readable shorthand. ``at(SENIOR)`` is every senior class;
    ``at(SENIOR, "Science")`` is the Science arm only."""
    return Placement(level=level, streams=tuple(streams))


@dataclass(frozen=True)
class SubjectSpec:
    name: str
    code: str
    #: Every placement this subject appears in. One Subject row, many classes.
    placements: tuple[Placement, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# The class ladder
# ---------------------------------------------------------------------------

CLASSES: tuple[ClassSpec, ...] = (
    ClassSpec("Pre-KG", NURSERY, 0),
    ClassSpec("KG 1", NURSERY, 1),
    ClassSpec("KG 2", NURSERY, 2),
    ClassSpec("KG 3", NURSERY, 3),
    ClassSpec("Primary 1", PRIMARY, 1),
    ClassSpec("Primary 2", PRIMARY, 2),
    ClassSpec("Primary 3", PRIMARY, 3),
    ClassSpec("Primary 4", PRIMARY, 4),
    ClassSpec("Primary 5", PRIMARY, 5),
    ClassSpec("Primary 6", PRIMARY, 6),
    ClassSpec("JSS 1", JUNIOR, 1),
    ClassSpec("JSS 2", JUNIOR, 2),
    ClassSpec("JSS 3", JUNIOR, 3),
    ClassSpec("SSS 1", SENIOR, 1, streams=("Arts", "Science")),
    ClassSpec("SSS 2", SENIOR, 2, streams=("Arts", "Science")),
    ClassSpec("SSS 3", SENIOR, 3, streams=("Arts", "Science")),
)


# ---------------------------------------------------------------------------
# Subjects
#
# Nursery through junior secondary is one list per level band, every class in
# the band taking the same set.
#
# Senior secondary is not: the school's two arms teach genuinely different
# subjects, so the senior placements are narrowed with at(SENIOR, "Science") /
# at(SENIOR, "Arts"). The five subjects both arms sit -- Mathematics, English
# Studies, Economics, Marketing and Civic Education -- stay one row each placed
# at(SENIOR), which puts them on both arms. Duplicating them per arm would mean
# renaming a subject twice and would make "how many subjects does SSS 2 Science
# take?" a question about our data model rather than about the school.
#
# Eleven subjects per arm. A subject that does not appear in an arm's list is
# simply not placed there -- which is why several subjects that run through
# junior secondary (Digital Literacy, Nigeria History, Social & Citizenship
# Studies, Basic Science Technology & PHE, Cultural and Creative Art, Religion
# Studies, Home Economics) stop at JSS 3. The senior arms pick their own
# equivalents up again by name where the school teaches one: Religion Studies
# gives way to Christian Religious Knowledge on the Arts arm, Home Economics to
# Home Management.
# ---------------------------------------------------------------------------

SUBJECTS: tuple[SubjectSpec, ...] = (
    # --- Nursery ----------------------------------------------------------
    SubjectSpec("Number Work, Counting & Shapes", "NUM", (at(NURSERY),)),
    SubjectSpec("Letter Work, Reading & Phonics", "LET", (at(NURSERY),)),
    SubjectSpec("Personal Social Habits", "PSH", (at(NURSERY),)),
    SubjectSpec("Emotional Development", "EMD", (at(NURSERY),)),
    SubjectSpec("Science & Health Habits", "SHH", (at(NURSERY),)),
    SubjectSpec("Writing Skills", "WRT", (at(NURSERY),)),
    SubjectSpec("Rhymes & Poems", "RHY", (at(NURSERY),)),

    # --- Nursery through junior secondary ----------------------------------
    SubjectSpec("Religion Studies", "REL", (at(NURSERY), at(PRIMARY), at(JUNIOR))),

    # --- Primary and junior secondary --------------------------------------
    SubjectSpec("Home Economics", "HEC", (at(PRIMARY), at(JUNIOR))),
    SubjectSpec("Cultural and Creative Art", "CCA", (at(PRIMARY), at(JUNIOR))),

    # --- Primary only ------------------------------------------------------
    SubjectSpec("Social Studies", "SST", (at(PRIMARY),)),
    SubjectSpec("Basic Science & Technology", "BST", (at(PRIMARY),)),
    SubjectSpec("History", "HIS", (at(PRIMARY),)),

    # --- Junior secondary only ---------------------------------------------
    SubjectSpec("Basic Science, Technology & PHE", "BTP", (at(JUNIOR),)),
    SubjectSpec("Social & Citizenship Studies", "SCS", (at(JUNIOR),)),
    SubjectSpec("Nigeria History", "NHI", (at(JUNIOR),)),
    SubjectSpec("Digital Literacy", "DIG", (at(JUNIOR),)),

    # --- Senior secondary: shared core -------------------------------------
    # One row each, at(SENIOR) with no arm named, so both arms get them.
    SubjectSpec("Mathematics", "MTH", (at(PRIMARY), at(JUNIOR), at(SENIOR))),
    SubjectSpec("English Studies", "ENG", (at(PRIMARY), at(JUNIOR), at(SENIOR))),
    SubjectSpec("Economics", "ECO", (at(SENIOR),)),
    SubjectSpec("Marketing", "MKT", (at(SENIOR),)),
    SubjectSpec("Civic Education", "CIV", (at(PRIMARY), at(SENIOR))),

    # --- Senior secondary: the Science arm ---------------------------------
    SubjectSpec("Chemistry", "CHE", (at(SENIOR, "Science"),)),
    SubjectSpec("Biology", "BIO", (at(SENIOR, "Science"),)),
    SubjectSpec("Physics", "PHY", (at(SENIOR, "Science"),)),
    SubjectSpec("Geography", "GEO", (at(SENIOR, "Science"),)),
    SubjectSpec("Livestock", "LIV", (at(SENIOR, "Science"),)),
    # Taught the whole way up, but only the Science arm carries it at senior.
    SubjectSpec(
        "Agriculture", "AGR", (at(PRIMARY), at(JUNIOR), at(SENIOR, "Science"))
    ),

    # --- Senior secondary: the Arts arm ------------------------------------
    SubjectSpec("Commerce", "COM", (at(SENIOR, "Arts"),)),
    SubjectSpec("Accounting", "ACC", (at(SENIOR, "Arts"),)),
    SubjectSpec("Government", "GOV", (at(SENIOR, "Arts"),)),
    SubjectSpec("Literature in English", "LIT", (at(SENIOR, "Arts"),)),
    SubjectSpec("Christian Religious Knowledge", "CRK", (at(SENIOR, "Arts"),)),
    SubjectSpec("Home Management", "HOM", (at(SENIOR, "Arts"),)),
)


def classes_for(spec: SubjectSpec, classes) -> list:
    """Every class in ``classes`` that ``spec`` should be attached to."""
    return [
        klass
        for klass in classes
        if any(placement.matches(klass) for placement in spec.placements)
    ]


# ---------------------------------------------------------------------------
# Reading a class name
#
# The ladder above is also the best guess anyone can make about a class name
# typed by hand: a school that writes "JSS 2" means the junior band, second
# year, and making them then pick both from dropdowns is two decisions the
# software could have made itself.
#
# These are *defaults*, never overrides. The bulk-add form only consults them
# for a row where the field was left blank, and whatever they return lands in
# an ordinary form field the person can see and correct before saving. A wrong
# guess therefore costs one correction, and never a wrong record saved
# silently.
# ---------------------------------------------------------------------------

#: Checked in order, first hit wins -- so the specific spellings ("sss", "jss")
#: are ahead of the looser ones they contain. Matched against whole words, not
#: substrings: "Primary 3" must not be read as a senior class because "pry"
#: happens to appear inside some other word.
_LEVEL_KEYWORDS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("sss", "ss", "senior"), SENIOR),
    (("jss", "js", "junior"), JUNIOR),
    (("primary", "pry", "grade", "standard"), PRIMARY),
    (
        ("nursery", "kg", "kindergarten", "creche", "playgroup", "reception",
         "toddler", "pre", "prekg", "preschool"),
        NURSERY,
    ),
)

#: "Basic" is the one spelling that cannot be read from the word alone: Basic 1
#: to 6 is primary and Basic 7 to 9 is junior secondary, so it is decided by
#: the number beside it.
_BASIC_SPLIT = 6


def _words(name: str) -> list[str]:
    """The name as lowercase words, with punctuation treated as a space.

    "Pre-KG" becomes ["pre", "kg"] rather than ["pre-kg"], which is what lets
    a single keyword list cover every way a school spells the same rung.
    """
    return [word for word in re.split(r"[^a-z0-9]+", name.lower()) if word]


def infer_year(name: str) -> int | None:
    """The year within the level, read off the end of the name.

    "Primary 4" is 4; "JSS 2A" is 2. Returns ``None`` when there is no number
    to read -- "Reception", "Pre-KG" -- and the caller keeps its own default.
    """
    numbers = re.findall(r"\d+", name)
    if not numbers:
        return None
    year = int(numbers[-1])
    # A year outside this range is not a year -- it is an admission number or a
    # year of entry that has ended up in the name field.
    return year if 0 <= year <= 20 else None


def infer_level(name: str) -> int | None:
    """Which band a class name belongs to, or ``None`` if it cannot be read."""
    words = _words(name)
    if not words:
        return None

    if "basic" in words:
        year = infer_year(name)
        if year is None:
            return PRIMARY
        return PRIMARY if year <= _BASIC_SPLIT else JUNIOR

    for keywords, level in _LEVEL_KEYWORDS:
        if any(keyword in words for keyword in keywords):
            return level
    return None


def ladder_presets() -> list[dict]:
    """The class ladder, grouped into the batches a school actually adds.

    Returned as plain dicts because this is rendered into the bulk-add page as
    JSON for a preset button: one press fills the rows it would otherwise take
    to type the whole band. The rung list is :data:`CLASSES`, so the front page
    of the app and the seeding command can never describe different ladders.

    An arm'd rung ("SSS 1" with Arts and Science) expands to one row per arm,
    which is what the school ends up with either way.
    """
    groups: dict[int, list[dict]] = {}
    for spec in CLASSES:
        rows = groups.setdefault(spec.level, [])
        for stream in spec.streams or ("",):
            rows.append(
                {
                    "name": spec.name,
                    "level": str(spec.level),
                    "year_in_level": str(spec.year_in_level),
                    "stream": stream,
                }
            )
    return [
        {"label": Level(level).label, "rows": rows}
        for level, rows in sorted(groups.items())
    ]


def subject_presets() -> list[dict]:
    """The seed subject sets, grouped by the band they are taught in.

    Same idea as :func:`ladder_presets`, for the subjects screen. A subject
    that spans several bands (Mathematics runs from Primary to SSS) appears
    under each band it is placed at -- the form de-duplicates by name before
    saving, so pressing two bands in a row cannot create it twice.
    """
    groups: dict[int, list[dict]] = {}
    for spec in SUBJECTS:
        for placement in spec.placements:
            rows = groups.setdefault(placement.level, [])
            if any(row["name"] == spec.name for row in rows):
                continue
            rows.append({"name": spec.name, "code": spec.code})
    return [
        {"label": Level(level).label, "rows": rows}
        for level, rows in sorted(groups.items())
    ]
