"""Move every school registered with BulkSMS Nigeria onto Termii.

The previous migration changed the *default* gateway, which only affects rows
created from now on. Existing schools -- the pilot included -- would still be
pointed at BulkSMS Nigeria, who declined to carry send-on-behalf-of-schools
traffic at all. Leaving them there means their next batch goes to a gateway
that will refuse it.

**Approval is reset to pending, deliberately.** A Sender ID approval is a fact
about one gateway: BulkSMS Nigeria having approved "Fulfilled" says nothing
about whether Termii has. Carrying the approved status across would have the
platform claim a registration that does not exist, and the first batch would
bounce -- which is precisely what ``SchoolMessagingConfig.is_usable`` exists to
prevent. So each moved row keeps its Sender ID and its history, and goes back
to pending with a note saying what to do.

That does mean a school cannot send until a platform administrator re-approves
it under Termii. That is the correct state: until Termii has registered the
name, the school genuinely cannot send under it, and a platform that says
otherwise is lying in the direction that costs money.

Schools on the console provider, on Africa's Talking, or already on Termii are
left alone. Rows that were pending, rejected or suspended keep their status and
their note -- there is no approval to withdraw and no reason to overwrite a
rejection someone recorded.
"""

from django.db import migrations

#: What the moved rows are told, and what the school sees on its identity
#: screen. Written for the proprietor, not for us.
MOVE_NOTE = (
    "Moved to Termii, our new SMS gateway. Your Sender ID is unchanged; it "
    "needs registering with Termii before messages can go out under it again."
)


def move_to_termii(apps, schema_editor):
    Config = apps.get_model("messaging", "SchoolMessagingConfig")

    # Only the approved ones lose their status: those are the rows that would
    # otherwise send under a name Termii has never seen.
    Config.objects.filter(provider="bulksmsnigeria", status="approved").update(
        provider="termii",
        status="pending",
        status_note=MOVE_NOTE,
        approved_by=None,
        approved_at=None,
    )
    # Everything else that was on BulkSMS Nigeria just changes gateway. A
    # rejection or a suspension is a decision somebody recorded, and this
    # migration has no business overwriting the reason they gave.
    Config.objects.filter(provider="bulksmsnigeria").update(provider="termii")


def move_back(apps, schema_editor):
    """Reverse: put them back on BulkSMS Nigeria, still pending.

    Deliberately does not restore the approval it cleared. Approvals are not
    recoverable from this table once ``approved_by`` and ``approved_at`` are
    gone, and inventing one on the way back would recreate the exact problem
    the forward migration exists to avoid.
    """
    Config = apps.get_model("messaging", "SchoolMessagingConfig")
    Config.objects.filter(provider="termii").update(provider="bulksmsnigeria")


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0003_message_purpose_alter_schoolmessagingconfig_provider"),
    ]

    operations = [
        migrations.RunPython(move_to_termii, move_back),
    ]
