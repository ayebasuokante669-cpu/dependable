"""Resolving which Sender ID a school's message goes out under.

One module, because "who is this message from?" has to be answered identically
in three places that must never disagree: the compose screen deciding whether
Send is even possible, the dispatcher stamping the batch, and the provider
putting a name in the request.

The rule, in order:

1. an approved config for this exact branch, if the school has set one up;
2. otherwise the school's approved school-wide config;
3. otherwise nothing -- and nothing means *refuse*, never a fallback.

That last point is the whole design. A school with no approved Sender ID must
not send under the platform's name, under a blank sender, or under another
school's: the first two get the batch rejected by the gateway or ignored by
parents, and the third is a tenancy breach that a parent would see on their own
handset. :class:`SenderIdentityUnavailable` carries a sentence a proprietor can
act on instead.

**The answer depends on the channel.** SMS needs the approved Sender ID.
WhatsApp needs the school's WhatsApp set up and approved by Meta -- a separate
approval on the same config row, and the SMS Sender ID's status does not enter
into it. A school waiting on Meta can still send SMS; a school with WhatsApp
approved and a Sender ID still pending can still send WhatsApp.
"""

from __future__ import annotations

from django.db.models import Q

from .models import SchoolMessagingConfig, SenderIdStatus
from .providers import Channel, SenderIdentity


class SenderIdentityUnavailable(Exception):
    """No approved identity exists for this school and channel, so nothing may be sent.

    Deliberately not a subclass of ``ProviderNotConfigured``: that one means the
    *platform* is missing credentials and only the platform can fix it. This
    one means the *school* is not registered yet, which is a different person's
    problem and a different sentence on screen.
    """

    def __init__(self, message: str, *, config: SchoolMessagingConfig | None = None):
        super().__init__(message)
        #: The unusable config, when one exists. The screens use it to show the
        #: rejection note rather than only "not set up".
        self.config = config


def config_for(school, branch=None) -> SchoolMessagingConfig | None:
    """The config governing ``branch``, approved or not.

    Returns the branch's own row in preference to the school-wide one, so a
    school that gives one campus its own Sender ID gets it, and every other
    campus keeps the default. Unapproved rows are returned too -- the caller
    needs them to explain *why* sending is blocked.

    Goes through ``all_objects`` and filters by school explicitly: this is
    called from the send path, where the caller has already been scoped to the
    branch they are messaging, and re-applying tenant scoping here would make a
    platform-owner-triggered send resolve to nothing.
    """
    if school is None:
        return None

    school_id = getattr(school, "pk", school)
    branch_id = getattr(branch, "pk", branch)

    # `branch_id__in=[branch_id, None]` looks like it would work and does not:
    # SQL's IN never matches NULL, so the school-wide row -- the one almost
    # every school actually has -- would be invisible. It has to be an explicit
    # IS NULL.
    scope = Q(branch__isnull=True)
    if branch_id:
        scope |= Q(branch_id=branch_id)

    # select_related the school: resolve() needs its name for the identity's
    # log label and for every refusal message, and a second query for a string
    # would be paid on every send.
    rows = list(
        SchoolMessagingConfig.all_objects.select_related("school")
        .filter(school_id=school_id)
        .filter(scope)
    )
    if not rows:
        return None
    # Branch-specific first; the school-wide row is the fallback.
    rows.sort(key=lambda row: 0 if row.branch_id == branch_id else 1)
    return rows[0]


def resolve(school, branch=None, *, channel: str = Channel.SMS) -> SenderIdentity:
    """The identity ``branch`` sends under on ``channel``, or refuse with a reason.

    A branch's own config row wins over the school-wide one for WhatsApp just
    as it does for SMS: a campus with a row of its own is set up by that row,
    whole.

    :raises SenderIdentityUnavailable: when there is no approved identity.
    """
    config = config_for(school, branch)
    school_name = _school_name(school, config)

    if channel == Channel.WHATSAPP:
        return _resolve_whatsapp(config, school_name)

    if config is None:
        raise SenderIdentityUnavailable(
            f"{school_name} has no Sender ID set up yet, so no message can be "
            f"sent. A platform administrator registers one under Messaging "
            f"identity.",
        )

    if not config.is_usable:
        raise SenderIdentityUnavailable(
            _blocked_message(config, school_name), config=config
        )

    return SenderIdentity(
        sender_id=config.sender_id,
        provider_key=config.provider,
        api_key=config.api_key,
        account_reference=config.account_reference,
        school_name=school_name,
    )


def resolve_for_branch(branch, *, channel: str = Channel.SMS) -> SenderIdentity:
    """Convenience for the send path, which always has a branch in hand."""
    return resolve(branch.school_id, branch, channel=channel)


def _resolve_whatsapp(
    config: SchoolMessagingConfig | None, school_name: str
) -> SenderIdentity:
    if config is None or not config.is_whatsapp_set_up:
        raise SenderIdentityUnavailable(
            f"{school_name} has not set up WhatsApp, so nothing can be sent "
            f"over it. A platform administrator connects the school's WhatsApp "
            f"Business number under Messaging identity.",
            config=config,
        )
    if not config.is_whatsapp_usable:
        raise SenderIdentityUnavailable(
            _whatsapp_blocked_message(config, school_name), config=config
        )
    return SenderIdentity(
        # Not sent to a WhatsApp gateway -- there is no Sender ID on WhatsApp --
        # but it is still the school's messaging name, and it is what the
        # batch's ``sent_as`` snapshot and the console log carry.
        sender_id=config.sender_id,
        provider_key=config.whatsapp_provider,
        api_key=config.api_key,
        account_reference=config.account_reference,
        school_name=school_name,
        device_id=config.whatsapp_device_id,
    )


def _blocked_message(config: SchoolMessagingConfig, school_name: str) -> str:
    """Why this config cannot send, in a sentence naming who fixes it."""
    note = f" Reason given: {config.status_note}" if config.status_note else ""

    if config.status == SenderIdStatus.PENDING:
        return (
            f'{school_name}\'s Sender ID "{config.sender_id}" is still awaiting '
            f"approval, so nothing can be sent under it yet.{note}"
        )
    if config.status == SenderIdStatus.REJECTED:
        return (
            f'{school_name}\'s Sender ID "{config.sender_id}" was rejected, so '
            f"nothing can be sent under it. Register a different one.{note}"
        )
    if config.status == SenderIdStatus.SUSPENDED:
        return (
            f'{school_name}\'s Sender ID "{config.sender_id}" is suspended, so '
            f"sending is paused.{note}"
        )
    return (
        f"{school_name} has no approved Sender ID, so no message can be sent."
        f"{note}"
    )


def _whatsapp_blocked_message(config: SchoolMessagingConfig, school_name: str) -> str:
    """Why this school cannot send WhatsApp yet, naming who fixes it."""
    note = (
        f" Reason given: {config.whatsapp_status_note}"
        if config.whatsapp_status_note else ""
    )

    if config.whatsapp_status == SenderIdStatus.PENDING:
        return (
            f"{school_name}'s WhatsApp Business account is still awaiting "
            f"approval from WhatsApp, so nothing can be sent over WhatsApp "
            f"yet.{note}"
        )
    if config.whatsapp_status == SenderIdStatus.REJECTED:
        return (
            f"{school_name}'s WhatsApp Business account was not approved by "
            f"WhatsApp, so nothing can be sent over it.{note}"
        )
    if config.whatsapp_status == SenderIdStatus.SUSPENDED:
        return (
            f"{school_name}'s WhatsApp is suspended, so WhatsApp sending is "
            f"paused.{note}"
        )
    return (
        f"{school_name} has no approved WhatsApp account, so nothing can be "
        f"sent over WhatsApp.{note}"
    )


def _school_name(school, config: SchoolMessagingConfig | None) -> str:
    """The school's name for a message on screen, however it was passed in.

    ``resolve`` takes a School or a school id, because the send path has the id
    in hand from the branch and looking the row up again would be a query for a
    string. When only the id is available the name is fetched here -- on the
    refusal path only, which is the one place it has to read well.
    """
    name = getattr(school, "name", None)
    if name:
        return name

    if config is not None and config.school_id:
        return config.school.name

    school_id = getattr(school, "pk", school)
    if school_id:
        from apps.schools.models import School

        found = School.all_objects.filter(pk=school_id).values_list(
            "name", flat=True
        ).first()
        if found:
            return found
    return "This school"
