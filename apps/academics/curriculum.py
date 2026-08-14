"""The class ladder and subject sets used to seed a school.

This is the single source for seeding. Edit the tuples below and re-run
``manage.py seed_academics --replace-subjects``; nothing else needs touching.

A subject is listed once and attached to every class it is taught in. That is
the point of the many-to-many: "Religion Studies" runs from Pre-KG to SSS 3 as
one row, not twenty near-duplicates. Where a name differs between levels it is a
different subject -- "Social Studies" (primary) and "Social & Citizenship
Studies" (secondary) are separate rows, as are "History" and "Nigeria History".
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
# The secondary list is one set covering both junior and senior secondary, so
# it is placed at JUNIOR and SENIOR. No subject is currently arm-specific --
# SSS Arts and SSS Science carry identical subject lists. When that changes,
# narrow the placement: at(SENIOR, "Science").
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

    # --- Runs the whole way through ---------------------------------------
    SubjectSpec(
        "Religion Studies", "REL", (at(NURSERY), at(PRIMARY), at(JUNIOR), at(SENIOR))
    ),

    # --- Primary and secondary --------------------------------------------
    SubjectSpec("Mathematics", "MTH", (at(PRIMARY), at(JUNIOR), at(SENIOR))),
    SubjectSpec("English Studies", "ENG", (at(PRIMARY), at(JUNIOR), at(SENIOR))),
    SubjectSpec("Agriculture", "AGR", (at(PRIMARY), at(JUNIOR), at(SENIOR))),
    SubjectSpec("Home Economics", "HEC", (at(PRIMARY), at(JUNIOR), at(SENIOR))),
    SubjectSpec(
        "Cultural and Creative Art", "CCA", (at(PRIMARY), at(JUNIOR), at(SENIOR))
    ),

    # --- Primary only ------------------------------------------------------
    SubjectSpec("Social Studies", "SST", (at(PRIMARY),)),
    SubjectSpec("Civic Education", "CIV", (at(PRIMARY),)),
    SubjectSpec("Basic Science & Technology", "BST", (at(PRIMARY),)),
    SubjectSpec("History", "HIS", (at(PRIMARY),)),

    # --- Secondary only (junior and senior) --------------------------------
    SubjectSpec(
        "Basic Science, Technology & PHE", "BTP", (at(JUNIOR), at(SENIOR))
    ),
    SubjectSpec(
        "Social & Citizenship Studies", "SCS", (at(JUNIOR), at(SENIOR))
    ),
    SubjectSpec("Nigeria History", "NHI", (at(JUNIOR), at(SENIOR))),
    SubjectSpec("Digital Literacy", "DIG", (at(JUNIOR), at(SENIOR))),
)


def classes_for(spec: SubjectSpec, classes) -> list:
    """Every class in ``classes`` that ``spec`` should be attached to."""
    return [
        klass
        for klass in classes
        if any(placement.matches(klass) for placement in spec.placements)
    ]
