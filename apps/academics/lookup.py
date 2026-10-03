"""Resolving the names a school types into the classes we actually hold.

Every bulk import that touches a class -- students, subjects, fee structures --
has the same problem: the spreadsheet says ``JSS 1A`` and the database holds a
row with ``name="JSS 1"`` and ``stream="A"``. This is the one place that maps
between them, so the three importers cannot disagree about what ``Primary 1``
means.

Shared rather than copied for a second reason: the ambiguity rule. A school that
runs ``Primary 1A`` and ``Primary 1B`` and writes ``Primary 1`` has asked a
question, and the only honest answer is to put it back to them. An importer that
quietly picked the first match would put a child in the wrong class, or price
the wrong one, and nothing on screen would say so.
"""

from __future__ import annotations

from apps.core.imports import normalise_header

from .models import Class, Level

#: Every spelling of a level band we accept in a "which classes?" cell, pointing
#: at the band. Schools write "primary", "Primary Section" or "PRY"; all three
#: mean the six primary classes.
_LEVEL_WORDS: dict[str, int] = {}
for _level in Level:
    for _spelling in (_level.label, _level.name.replace("_", " ")):
        _LEVEL_WORDS[normalise_header(_spelling)] = int(_level)
_LEVEL_WORDS.update(
    {
        "nursery": Level.NURSERY,
        "kg": Level.NURSERY,
        "kindergarten": Level.NURSERY,
        "pre school": Level.NURSERY,
        "primary": Level.PRIMARY,
        "pry": Level.PRIMARY,
        "jss": Level.JUNIOR_SECONDARY,
        "js": Level.JUNIOR_SECONDARY,
        "junior": Level.JUNIOR_SECONDARY,
        "junior secondary school": Level.JUNIOR_SECONDARY,
        "sss": Level.SENIOR_SECONDARY,
        "ss": Level.SENIOR_SECONDARY,
        "senior": Level.SENIOR_SECONDARY,
        "senior secondary school": Level.SENIOR_SECONDARY,
    }
)

#: What a school writes when it means "every class at this campus".
_EVERYTHING = {"all", "all classes", "every class", "everyone", "whole school"}


def level_for(text: str) -> int | None:
    """The band a cell names, or ``None`` if it does not name one."""
    return _LEVEL_WORDS.get(normalise_header(text))


def level_names() -> list[str]:
    """The band labels, for a template's drop-down."""
    return [label for _, label in Level.choices]


class ClassIndex:
    """The branch's classes, looked up the way a spreadsheet spells them.

    A school types ``JSS 1A``, ``JSS 1 A`` or just ``JSS 1``; all three should
    find the class. What must *not* happen is a silent guess when the name is
    genuinely ambiguous -- ``Primary 1`` where the branch runs ``Primary 1A``
    and ``Primary 1B`` is a question for the user, not for us.
    """

    def __init__(self, branch):
        self.branch = branch
        self._active: dict[str, list[Class]] = {}
        self._inactive: set[str] = set()
        self._by_level: dict[int, list[Class]] = {}

        for klass in Class.objects.filter(branch=branch):
            keys = {
                normalise_header(klass.display_name),
                normalise_header(f"{klass.name} {klass.stream}"),
                normalise_header(klass.name),
            }
            for key in keys:
                if not key:
                    continue
                if klass.is_active:
                    self._active.setdefault(key, []).append(klass)
                else:
                    self._inactive.add(key)
            if klass.is_active:
                self._by_level.setdefault(klass.level, []).append(klass)

    def resolve(self, text: str) -> tuple[Class | None, str | None]:
        """Return ``(class, error message)``; exactly one of the two is set."""
        key = normalise_header(text)
        matches = self._active.get(key, [])
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            names = ", ".join(sorted(k.display_name for k in matches))
            return None, (
                f"'{text}' matches more than one class ({names}). "
                f"Include the arm, e.g. '{matches[0].display_name}'."
            )
        if key in self._inactive:
            return None, (
                f"'{text}' is a class at {self.branch.name} but it is no longer "
                f"active. Reactivate it, or use a different class."
            )
        return None, (
            f"'{text}' is not a class at {self.branch.name}. Check the spelling, "
            f"or set the class up first."
        )

    def resolve_many(self, tokens: list[str]) -> tuple[list[Class], list[str]]:
        """Resolve a cell that holds several classes. Returns ``(classes, problems)``.

        Each token may be one class, a whole band ("Junior Secondary"), or
        "All". Bands are accepted because that is how schools describe a
        subject's reach -- "Mathematics: Primary" is one cell rather than six --
        and because the alternative is a template with one column per class,
        which no two schools would agree on.

        Order is preserved and duplicates are dropped, so naming a band and one
        of its classes is not an error.
        """
        found: dict[int, Class] = {}
        problems: list[str] = []

        for token in tokens:
            key = normalise_header(token)
            if key in _EVERYTHING:
                for klass in self.all_active:
                    found[klass.pk] = klass
                continue

            level = level_for(token)
            if level is not None:
                band = self._by_level.get(level, [])
                if not band:
                    problems.append(
                        f"'{token}' is a level with no classes set up at "
                        f"{self.branch.name} yet."
                    )
                for klass in band:
                    found[klass.pk] = klass
                continue

            klass, problem = self.resolve(token)
            if problem:
                problems.append(problem)
            else:
                found[klass.pk] = klass

        return list(found.values()), problems

    @property
    def all_active(self) -> list[Class]:
        """Every active class at the branch, in admission order."""
        seen = {
            klass.pk: klass
            for matches in self._active.values() for klass in matches
        }
        return sorted(
            seen.values(),
            key=lambda k: (k.level, k.year_in_level, k.stream, k.name),
        )

    @property
    def names(self) -> list[str]:
        """Every active class name, for a template's reference sheet."""
        return [klass.display_name for klass in self.all_active]
