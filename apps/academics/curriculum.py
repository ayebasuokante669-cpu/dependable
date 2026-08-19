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
