"""Record the pilot school's module state, rather than leaving it to the default.

Messaging and admissions are both ``default_on=False`` in the registry, so
Fulfilled Academy would read as having neither with no rows at all. This writes
the two rows anyway, and that is the point: a default is what the platform does
when nobody has decided, and somebody *has* decided here. Leaving it implied would
mean a later change of default silently switched a live school's features on, in
the middle of a term, with an SMS bill attached.

Reversible: the rows go away and the school falls back to the registry default,
which is the state it was in before this ran.

Keyed on the school's name and guarded by ``exists()`` on each row, so it is safe
on a database where the school is absent (every test database, and any developer's
own) and safe to run twice.
"""

from django.db import migrations

#: The school this is about, and what was decided for it. Written as data rather
#: than as code so the intent survives being read a year from now.
PILOT_SCHOOL = "Fulfilled Academy"
PILOT_STATE = {
    # The client is not set up to send yet: no approved Sender ID, and every
    # message costs the platform money.
    "messaging": False,
    # The pipeline, the admission fees and the school's public enquiry page. Not
    # part of the pilot.
    "admissions": False,
}


def apply_pilot_state(apps, schema_editor):
    School = apps.get_model("schools", "School")
    SchoolModule = apps.get_model("schools", "SchoolModule")

    # A historical model, so no custom managers and no tenant scoping -- which is
    # what a migration needs: there is no request, and therefore no tenant.
    school = School.objects.filter(name=PILOT_SCHOOL).first()
    if school is None:
        return

    for key, enabled in PILOT_STATE.items():
        SchoolModule.objects.update_or_create(
            school=school, key=key, defaults={"enabled": enabled}
        )


def undo_pilot_state(apps, schema_editor):
    School = apps.get_model("schools", "School")
    SchoolModule = apps.get_model("schools", "SchoolModule")

    school = School.objects.filter(name=PILOT_SCHOOL).first()
    if school is None:
        return
    SchoolModule.objects.filter(school=school, key__in=PILOT_STATE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("schools", "0003_schoolmodule"),
    ]

    operations = [
        migrations.RunPython(apply_pilot_state, undo_pilot_state),
    ]
