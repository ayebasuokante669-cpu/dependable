"""Removing a school, and everything that belonged to it.

Four foreign keys in this codebase are ``PROTECT``, and every one of them is
right: a student with payments against their name must not vanish from under the
money, a term with payments against it is history, a class with students on it
must not disappear from under them, and a WhatsApp template that has carried
messages is retired by changing its status rather than by deleting the record.
Those rules are what stop an ordinary afternoon's tidying from destroying a
payment record.

They are also why deleting a *school* in the admin was unusable. Django's delete
view walks the object graph, meets the first ``PROTECT``, and refuses — it lists
the protected rows and offers no button at all. The only way through was to go
and delete the payments, then the students, then the classes, by hand, one
changelist at a time, and for a school with four hundred students that is not a
workflow, it is a punishment.

**Closing a tenant is a different decision from tidying one.** The protection
still belongs on the models, where it guards the everyday case. What belongs
here is a single deliberate teardown, confirmed once, that takes the whole
school: all or nothing, in one transaction.

Nothing here is hardcoded
-------------------------
The order the protected rows have to go in is not written down. It is read off
the database's own refusal: ``ProtectedError`` names exactly which rows stood in
the way, so the teardown deletes those first and tries again. A fifth ``PROTECT``
added to a model next year is handled without anybody remembering to come back
to this file — which is precisely the failure being fixed here, so it is the one
worth designing out rather than re-creating one list further down.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from django.db import connection, transaction
from django.db.models import ProtectedError, RestrictedError
from django.utils.text import capfirst

from .models import School

#: How many layers of protection to peel before giving up. This schema is three
#: deep (payments guard students, students guard classes); the headroom is for a
#: schema that grows. The bound exists so that a cycle fails loudly instead of
#: spinning, and so a bug here can never turn into an unbounded delete loop.
MAX_PASSES = 12


class CouldNotRemove(RuntimeError):
    """More layers of protection than the teardown is willing to peel.

    Raised inside the transaction, so nothing has been removed by the time the
    caller sees it.
    """


def remove(school: School) -> Counter:
    """Delete ``school`` and every row that belonged to it.

    One transaction: a teardown that stopped halfway would leave a school's
    students alive with no school to belong to, which is worse than either
    outcome. Returns what was removed, keyed by model, for the message the admin
    shows afterwards.
    """
    removed: Counter = Counter()
    with transaction.atomic():
        _delete(school, removed, depth=0)
    return removed


def _delete(target, removed: Counter, depth: int) -> None:
    """Delete one instance or queryset, clearing whatever refuses it first.

    ``target`` is anything with Django's ``delete()`` contract — a model
    instance or a queryset — because the recursion needs both: the school
    itself, then the rows that blocked it.
    """
    if depth > MAX_PASSES:
        raise CouldNotRemove(
            f"Gave up removing {target!r} after {MAX_PASSES} passes. Something "
            f"is protecting a row in a cycle; nothing has been deleted."
        )

    savepoint = transaction.savepoint()
    try:
        _, counts = target.delete()
    except (ProtectedError, RestrictedError) as refusal:
        # Nothing from the refused attempt is kept. Django's collector may have
        # already issued some of the cascade before it reached the protected
        # edge, and the retry below has to start from the state the refusal was
        # raised against rather than from a half-emptied one.
        transaction.savepoint_rollback(savepoint)

        for model, pks in _blockers_by_model(refusal).items():
            # ``_base_manager`` rather than ``objects``: every tenant model's
            # default manager filters by the *caller's* school, and a teardown
            # run by a platform account has no school of its own to filter by.
            _delete(model._base_manager.filter(pk__in=pks), removed, depth + 1)

        _delete(target, removed, depth + 1)
    else:
        transaction.savepoint_commit(savepoint)
        removed.update(counts)


def _blockers_by_model(refusal) -> dict[type, list]:
    """The rows that refused the delete, grouped by the model they belong to.

    Grouped so each model goes in one query rather than one per row — a school
    closing can have thousands of payments, and ``for obj in objs: obj.delete()``
    would be thousands of round trips and thousands of cascade collections.
    """
    objects = getattr(refusal, "protected_objects", None)
    if objects is None:
        objects = getattr(refusal, "restricted_objects", ())

    grouped: dict[type, list] = defaultdict(list)
    for obj in objects:
        # The concrete model, so a deferred or proxy instance is grouped with
        # the rows it shares a table with.
        grouped[obj._meta.concrete_model].append(obj.pk)
    return grouped


# ---------------------------------------------------------------------------
# What a teardown would take
# ---------------------------------------------------------------------------


def countable_relations():
    """The reverse relations from ``School`` that can actually be counted.

    Read off ``School``'s own relations rather than from a list kept here, for
    the same reason the teardown order is: every tenant model carries a
    ``school`` foreign key by inheritance, so a new app's model is included the
    day it is written.

    Two are skipped. A many-to-many has no rows of its own worth announcing --
    its join rows go when the rows on either side of them do. And a model with
    no table in this database is skipped rather than queried: a model can be
    registered without being migrated (the tenancy suite registers one and
    creates its table for the length of a single test case), and a confirmation
    page that 500s because one relation is odd is a worse failure than a count
    that leaves it out. The teardown itself is unaffected -- it goes through
    Django's cascade, which would raise on a missing table inside the
    transaction and leave nothing deleted.
    """
    tables = set(connection.introspection.table_names())
    for relation in School._meta.related_objects:
        if relation.many_to_many:
            continue
        model = relation.related_model
        if model._meta.db_table not in tables:
            continue
        yield relation, model


def summarise(school: School) -> list[tuple[str, int]]:
    """Every row that would go, counted per model, biggest first.

    Counted rather than listed. The stock confirmation page renders every object
    by name, and a school with four hundred students and two thousand payments
    turns the one question being asked — *are you sure* — into a page nobody
    reads to the bottom of.
    """
    counts: list[tuple[str, int]] = []
    for relation, model in countable_relations():
        count = model._base_manager.filter(**{relation.field.name: school}).count()
        if not count:
            continue
        opts = model._meta
        label = opts.verbose_name if count == 1 else opts.verbose_name_plural
        counts.append((capfirst(str(label)), count))

    return sorted(counts, key=lambda row: (-row[1], row[0]))


def describe(removed: Counter) -> str:
    """What was removed, as a sentence for the admin's own message log."""
    if not removed:
        return "Nothing else belonged to it."
    parts = [
        f"{count} {label.split('.')[-1].replace('_', ' ')}"
        for label, count in sorted(removed.items(), key=lambda row: -row[1])
    ]
    return "Also removed: " + ", ".join(parts) + "."
