
# SCHOOLCORD

Multi-tenant school management SaaS.

> **On the name.** This was built under two earlier names — "Dependable" (an
> internal codename) and "Fulfilled Lite" (a working title). Both are gone from
> everything a user or an admin sees. The product is **SCHOOLCORD**, and the
> name now lives in exactly one file: `apps/core/branding.py`. A handful of
> internal identifiers still carry the codename — the Postgres database name,
> a contextvar, and `DependableUserManager`, which is serialised into
> `accounts/0001_initial.py` — and are left alone deliberately: nobody sees
> them, and rewriting an applied migration to rename a manager class buys
> nothing. See [Branding and identity](#branding-and-identity).

- **Step 1 — foundation:** tenancy, a custom user model, the query-scoping
  pattern, the design-system shell, and the admin.
- **Step 2 — academic setup:** classes and subjects per branch, with management
  screens.
- **Step 3 — fee structures:** terms, and what each class owes per term.
- **Step 4 — student records:** the roster, entered by hand or imported from a
  spreadsheet, with each student's fee position derived from their class.
- **Step 5 — the public side:** landing page, signup, login with a per-role
  landing, password reset, and the onboarding checklist that ties the four
  setup steps together.
- **Step 6 — parent messaging:** SMS/WhatsApp to parents, each school sending
  under its own registered Sender ID.
- **Step 7 — payments:** money recorded against a student, with every balance
  derived from confirmed payments.

## Running it

```bash
python -m venv venv
venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt

copy .env.example .env           # macOS/Linux: cp .env.example .env

python manage.py migrate
python manage.py bootstrap_tenant     # optional demo school + one user per role
python manage.py runserver
```

To load the pilot school's academic setup:

```bash
python manage.py seed_academics --create-school       # Fulfilled Academy / Main Campus
python manage.py seed_fees                            # First Term 2025/2026 pricing
python manage.py seed_students                        # 60 students across 9 classes
python manage.py bootstrap_tenant --name "Fulfilled Academy" --branch "Main Campus"
```

Then open http://127.0.0.1:8000/.

`bootstrap_tenant` creates a school, a branch, and four accounts — all with the
password `dependable`:

| Username                       | Role                      | Sees                       |
| ------------------------------ | ------------------------- | -------------------------- |
| `platform.owner`               | Platform Owner            | every school               |
| `northgate-academy.owner`      | School Owner              | one school, all branches   |
| `northgate-academy.principal`  | Principal / Branch Admin  | one branch                 |
| `northgate-academy.bursar`     | Bursar                    | one branch                 |

Run it again with `--name "Rival College"` to create a second tenant and watch
the isolation hold. For a superuser: `python manage.py createsuperuser`.

### CSS

The compiled stylesheet is committed, so the project runs without Node. To
change the design tokens, edit `static/src/app.css` and rebuild:

```bash
npm install
npm run build:css     # or: npm run watch:css
```

### Database

Postgres is the target; SQLite is the local fallback.

- **Neither `DATABASE_URL` nor `DB_NAME` set** → SQLite.
- **Postgres configured but unreachable** → SQLite, with a warning on stderr.
- **`DB_ENGINE=postgres`** → Postgres required, no fallback (what `config.settings.prod` uses).
- **`DB_ENGINE=sqlite`** → SQLite always.

Connection poolers (Supabase, PgBouncer) are detected by host or port `6543` and
get `DISABLE_SERVER_SIDE_CURSORS = True`, because transaction-mode pooling hands
each statement a different backend and breaks Django's server-side cursors.
Override the detection with `DB_POOLED=1` / `DB_POOLED=0`.

Settings live in `config/settings/`: `base.py`, then `dev.py` (the default) and
`prod.py`. `manage.py` defaults to `config.settings.dev`; `wsgi.py`/`asgi.py`
default to `config.settings.prod`.

## Tests

```bash
python manage.py test apps
```

276 tests, covering the parts that must never regress: what each role can see,
what each role may change, that a fee total always equals its live line items,
that a student's expected fee is always read from their class rather than stored
on them, that each senior arm carries exactly the subjects the school named,
that a spreadsheet import reports every bad row, imports the good ones, and
writes all-or-nothing, and that a signup builds exactly one school, branch and
owner while each role lands on its own dashboard. The negative-path tests deliberately trigger
403s and 404s, so Django logs tracebacks during a passing run.

---

## How the tenancy works

Isolation is enforced in the model layer, not per view.

1. `TenantMiddleware` reads `request.user` and publishes a `TenantContext`
   (school, branch, role) into a `contextvars` variable for the request.
2. Tenant-scoped managers read that context in `get_queryset()` and filter
   before any view code runs.

So a view writes `Student.objects.all()` and gets only its own tenant's rows.
There is nothing to remember and nothing to forget.

### Adding a tenant-scoped model

Subclass `TenantScopedModel` and you're done:

```python
from apps.core.models import TenantScopedModel

class Payment(TenantScopedModel):     # brings school + branch FKs and indexes
    reference = models.CharField(max_length=64)
```

Use `BranchScopedModel` instead when the record must live at one campus, as
`Class`, `Subject` and `Student` do. It makes `branch` required and derives
`school` from it, so a platform owner — who has no school of their own — can
still create rows.

You get:

- `school` and `branch` foreign keys (`branch` nullable — some records belong to
  the school as a whole).
- `Payment.objects` — filtered by the active tenant automatically.
- `Payment.all_objects` — never filtered, for jobs and cross-tenant reporting.
- `save()` stamps `school`/`branch` from the active context when left blank.
- `created_at` / `updated_at`.

### Roles and what they see

| Role                         | Scope    | Rows visible                                   |
| ---------------------------- | -------- | ---------------------------------------------- |
| `platform_owner`             | platform | everything                                     |
| `school_owner`               | school   | all branches of their school                   |
| `principal` (branch admin)   | branch   | their branch + school-wide rows (`branch=NULL`) |
| `bursar`                     | branch   | their branch + school-wide rows                |

Defined once in `apps/core/roles.py`. Two rules worth knowing:

- **No context = no filtering.** Outside the request cycle (shell, migrations,
  management commands) queries are unfiltered — otherwise `migrate` could not
  see its own rows. Background jobs that should act as a tenant must say so:
  `with scope_to(school_id=..., branch_id=..., role=...):`.
- **Misconfiguration fails closed.** An unknown role, or a branch-scoped role
  with no branch assigned, sees nothing rather than defaulting to wider access.

Escape hatches, both deliberately visible in review:

```python
from apps.core.tenancy import unscoped, scope_to

with unscoped():                  # cross-tenant reporting, support tooling
    Student.objects.count()

with scope_to(school_id=7, branch_id=3, role=Role.BURSAR):
    ...                           # background job acting as a tenant
```

### Capabilities: what a role may *do*

Scoping answers "which rows?". Capabilities (`apps/core/permissions.py`) answer
"which actions?" — so a bursar can have full visibility of the academic setup
while still being unable to change it.

| Capability         | Platform owner | School owner | Principal | Bursar |
| ------------------ | :------------: | :----------: | :-------: | :----: |
| `view_academics`   | ✓ | ✓ | ✓ | ✓ |
| `manage_academics` | ✓ | ✓ | ✓ | — |
| `view_fees`        | ✓ | ✓ | ✓ | ✓ |
| `manage_fees`      | ✓ | ✓ | ✓ | — |
| `view_students`    | ✓ | ✓ | ✓ | ✓ |
| `manage_students`  | ✓ | ✓ | ✓ | — |

A bursar collects against the fee structure but does not decide it, and finds
students in order to take money from them rather than to enrol or remove them —
so they read all three layers and change none. The money-*movement* capabilities
arrive with the payments layer.

Grants live in one table, `ROLE_CAPABILITIES`, never in an ad-hoc check inside a
view. Enforce with the mixin, and hide the controls to match:

```python
class ClassCreateView(CapabilityRequiredMixin, CreateView):
    capability = Capability.MANAGE_ACADEMICS
```

```html
{% if "manage_academics" in capabilities %} ... {% endif %}
```

The mixin raises 403 for a signed-in account that lacks the capability (rendered
by `templates/403.html`) and redirects anyone signed out to the login page.
Hiding a button is presentation; the mixin is the enforcement, and it blocks POST
as well as GET.

### Permission role vs. job title

Two separate fields on `User`, on purpose:

- **`role`** — the permission role. Controls what the account can see. One of the
  four above.
- **`job_title`** — free text, e.g. "Head of Mathematics". Descriptive only,
  grants nothing.

Promoting someone's job title is an HR change, not an access change.

### Why `User.objects` is not scoped

Authentication backends and `createsuperuser` resolve users through the default
manager, before any tenant context exists — scoping it would break login. Use
`User.scoped` for tenant-facing staff listings; the admin is narrowed separately
by `TenantScopedAdminMixin`.

## Academic setup

Step 2 of onboarding, at `/academics/`. Both models are `BranchScopedModel`, so
every list and lookup is tenant-filtered without a line of filtering in any view.

**`Class`** — a teaching group. `name` holds the year without its arm ("SSS 2")
and `stream` holds the arm ("Science", "A"); `display_name` joins them the way
staff say it, appending a one-letter arm ("JSS 1A") and spacing a word one
("SSS 2 Science"). Splitting them is what lets every arm of a year sort together
and lets a subject attach to one arm but not the other.

Ordering is by `level` then `year_in_level`, so classes always list in admission
order — Pre-KG first, SSS 3 last. `Level` values are spaced (10/20/30/40) to
leave room for a band in between later without a data migration.

**`Subject`** — attached to classes many-to-many, because one subject genuinely
spans several. Mathematics is *one* row linked to Primary 1 through both arms of
SSS 3, not fifteen near-duplicates.

Class names are unique per branch, not per school, so every campus can run its
own "JSS 1".

### Seeding

`seed_academics` builds the ladder and subject list from
`apps/academics/curriculum.py` — one module, edit it and re-run:

```bash
python manage.py seed_academics --create-school
python manage.py seed_academics --replace-subjects   # after editing curriculum.py
```

It is idempotent, matching on (branch, name). For Fulfilled Academy that is 19
classes (13 single-arm plus three senior years × Arts/Science) and 34 subjects.

Thirty-four, not sixty-odd, because a subject taught at several levels is one
row: Religion Studies spans 13 classes, Mathematics spans 15. Where the name
differs by level it stays a separate subject — "Social Studies" (primary) and
"Social & Citizenship Studies" (secondary), "History" and "Nigeria History",
"Home Economics" (primary/junior) and "Home Management" (SSS Arts).

| Level | Subjects per class |
| ----- | -----------------: |
| Nursery | 8 |
| Primary | 10 |
| Junior Secondary | 10 |
| Senior Secondary | 11 per arm |

### The senior arms teach different subjects

Nursery through junior secondary is one list per band. Senior secondary is not:
SSS Science and SSS Arts carry **eleven subjects each, and only five of them
overlap**.

| | Subjects |
| --- | --- |
| Shared core | Mathematics, English Studies, Economics, Marketing, Civic Education |
| Science only | Chemistry, Biology, Physics, Agriculture, Geography, Livestock |
| Arts only | Commerce, Accounting, Government, Literature in English, Christian Religious Knowledge, Home Management |

Arm-specific subjects narrow their placement — `at(SENIOR, "Science")` — while
the shared five stay **one row each** at `at(SENIOR)` with no arm named, which
puts them on both. Duplicating them per arm would mean renaming a subject twice
and would make "how many subjects does SSS 2 Science take?" a question about our
data model rather than about the school.

The consequence worth knowing: several subjects that run through junior
secondary stop at JSS 3, because neither senior list includes them — Digital
Literacy, Nigeria History, Social & Citizenship Studies, Basic Science
Technology & PHE, Cultural and Creative Art, Religion Studies and Home
Economics. The senior arms pick their own equivalents up by name where the
school teaches one, which is why Christian Religious Knowledge and Home
Management are separate rows rather than senior placements of the junior
subjects.

## Fee structures

Step 3, at `/fees/`. Three models, all branch-owned:

```
Term  --<  FeeStructure  --<  FeeComponent
```

**`Term`** — one term of an academic year at a branch. Exactly one can be
current per branch, enforced by a partial unique index *and* by `save()`
standing the previous one down, so a stray script cannot leave two.

**`FeeStructure`** — the fee set for one class in one term, unique on
(class, term).

**`FeeComponent`** — a line item: name and amount, ordered by `position`.

### The total is never stored

`FeeStructure.total` is a property that sums the live components. There is no
total column, and a test asserts there never is one. A stored total is a second
source of truth that drifts the moment someone edits a line item, and
reconciling a payment against a stale figure costs a school real money.

The editor's live sum is display-only — the browser never posts a total, so a
JavaScript bug can degrade the typing experience but cannot change what a class
owes. The form and its line-item formset are validated together inside one
transaction, so a bad line can't leave a structure behind with no components.

### Screens

The list is grouped by level with per-level subtotals, defaults to the current
term, and ends with a "not yet priced" section — the question a school owner
actually has during onboarding. Terms themselves are managed in the Django
admin; say the word if they should get first-class screens too.

### Seeding

```bash
python manage.py seed_fees            # needs seed_academics to have run
python manage.py seed_fees --replace  # drop structures the pricing file dropped
```

`apps/fees/pricing.py` is the single source, the same way `curriculum.py` is for
academics. Fulfilled Academy's First Term 2025/2026 comes to 19 structures, 61
line items, ₦1,796,000 across the term.

Two things the pricing file handles deliberately:

- **One structure per class, never shared.** Primary 2–6 charge the same today,
  so they are written as one spec listing five class names — but the seeder
  creates five independent structures with their own components. Repricing
  Primary 3 leaves Primary 2 alone, and a test proves it.
- **`client_total` is a cross-check, never stored.** Where the school's written
  total disagrees with the line items, the seeder charges the line items and
  prints a warning. Primary 1 currently trips this: the line items sum to
  ₦102,000 against a written total of ₦104,000. The components are seeded as
  supplied and **no balancing line has been invented** — the ₦2,000 gap is a
  real question about the source data, and burying it in a fake component would
  make it unanswerable later.

## Student records

Step 4, at `/students/`. One model, `BranchScopedModel` like the rest, so a
principal's roster is their own campus without a line of filtering in any view.

**`Student`** — admission number, names, sex, dates of birth and admission,
status, and the parent/guardian contact the school actually calls about fees.

### Admission numbers are unique per branch, not globally

Two campuses of the same school both number their intake from 001, and neither
should have to renumber because the other got there first. The constraint is
`UniqueConstraint(Upper("admission_number"), "branch")` — case-insensitive, so
`FA/2025/001` and `fa/2025/001` collide, but the school's own casing is stored
as typed rather than rewritten. The form catches the clash first and reports it
on the field; the constraint is what makes it true regardless of how the row
arrives, which is what the Excel import leans on.

### The fee position is derived, never stored

A student's expected fee is the total of their class's `FeeStructure` for the
term their branch is currently in. It is computed in `apps/students/fees.py`,
and there is no fee column on `Student` — a test asserts there never is one.
Copying the figure onto the student would go stale the first time a class is
repriced and would then have to be corrected for every child in it by hand.

The list screen would make that derivation expensive if done per row, so
`fees.load(students)` fetches the current terms and the class totals in two
queries and answers for the whole page in memory. A test asserts a page of
twelve students costs the same as a page of two.

Payments do not exist yet, so `paid` is zero and every priced student reads as
**Unpaid** with the full term fee outstanding. That is honest rather than
decorative, and it is the seam the payments layer plugs into: fill
`FeeSchedule.payments` and the pills, balances and the "Payment history" card
on the detail page start telling the real story without those screens changing.
A student in a class with no structure for the term reads **No fees set**,
which is a setup gap the school needs to see — not a zero balance to be
reassured by.

### Screens

- **List** — searchable by name or admission number (whole or fragment, because
  staff quote either "FA/2025/014" or just "014"), filterable by class and
  status, 25 to a page. Defaults to active students; a roster that has run for
  years is mostly former ones.
- **Detail** — identity, class, guardian, and the fee position broken down to
  the line items behind it, with the payment-history slot laid out and labelled.
- **Add / edit** — manual entry. Branch is not a field: the class carries it, and
  asking for both invites the two to disagree. Removing a student offers
  "mark withdrawn" first, because deleting throws away the record that a payment
  will later need to hang off.
- **Import** — the bulk path for a school arriving with a roster already in a
  spreadsheet. Template, per-row validation report, then the write. See below.

Parent phone numbers are validated as Nigerian mobiles and stored in one
canonical form (`08034129876`), whichever of `0803 412 4567`, `+234 803 …` or
`234 …` was typed; the screens group them for reading and link them for calling.

### Excel import

At `/students/import/`, owner- and principal-level only (`MANAGE_STUDENTS`, so a
bursar gets a 403). Three screens, because the middle one is the product:

1. **Download** — `apps/students/workbook.py` builds an `.xlsx` from the column
   spec in `importer.py`: a header row, a guidance row showing the format of
   every field, drop-downs for Sex and Status, and a second sheet listing *that
   branch's* classes with the Class column validated against it. A school owner
   is offered one template per campus, because the class list differs.
2. **Upload and check** — every row is parsed and judged before anything is
   written. Nothing at all is saved by this step.
3. **Confirm** — the valid rows are written in one transaction.

**A bad row is not a bad file.** All-or-nothing sends the user back to fix one
cell at a time, so failure is per row: the report names the spreadsheet's own
row number, the column, and the reason — *"Row 14 — Admission Number
'FA/2025/001' already belongs to Chinaza Okonkwo; Class 'JS1' is not a class at
North"* — and the rows that passed can still be imported while the rest are
corrected. A row reports *all* of its problems at once, including the ones the
model finds, so the trip back to the spreadsheet happens once.

What is validated: the required fields (admission number, first and last name,
class, parent name and phone); the admission number against the branch's
existing roster *and* against the other rows of the same file, case-insensitively
like the constraint; the class against the branch's active classes, with
separate messages for unknown, inactive and ambiguous (`JSS 3` where the branch
runs `JSS 3A` and `JSS 3B`); sex, status and dates by parsing; and then the
model's own `full_clean` for phone format, email, lengths and the
born-after-admission rule, which are not restated here.

The realistic mess is handled rather than reported: columns are matched **by
heading, never by position**, with aliases (`Adm No`, `Surname`, `DOB`,
`Guardian`, `Phone`), so a school's own sheet imports; a title row above the
headings is found; whitespace is trimmed; blank rows anywhere are skipped;
columns we have no field for are ignored and listed; and a phone column Excel
turned numeric (`8031122334`) gets its leading zero back. A file that is not a
workbook, is not the template, or has no rows under its headings produces a
sentence on the form, never a 500.

Two things worth knowing about the shape of it:

- **`importer.py` never touches openpyxl; `workbook.py` never validates.** The
  reader hands over plain strings — dates as ISO — so validation is testable
  without building a workbook, and so the parsed rows survive the session.
- **The rows wait in the session, not a table.** An abandoned import leaves
  nothing to clean up. Validation then runs *twice*: once to draw the report,
  and again on confirm, because the roster can move while the report is on
  screen. A test covers exactly that race, and another asserts that a failure on
  the third student of a batch leaves the first two unwritten.

### Seeding

```bash
python manage.py seed_students            # needs seed_academics; seed_fees for fee positions
python manage.py seed_students --replace  # drop students the roster file dropped
```

`apps/students/roster.py` is the single source. Fulfilled Academy's pilot roster
is 60 students across 9 classes, ₦5,802,000 expected for First Term 2025/2026.

The data is deliberate rather than filler, because a roster of "Student One /
Student Two" hides the problems these screens exist to surface: admission
numbers carry their year of admission (so the same `001` recurs each year and
across branches), dates of birth match the class, two Okonkwo siblings sit in
Primary 1 and KG 2 sharing one guardian, and one student is withdrawn and one
inactive so the status filter has something real to filter.

## Parent messaging

At `/messaging/`. Entirely staff-side and outbound: **parents never sign in.**
The platform's whole relationship with a parent is an SMS or WhatsApp message
going out, and a row recording what became of it.

### The provider interface comes first

```
apps/messaging/providers/
  base.py             MessagingProvider: send(recipient, message, channel)
                      + SenderIdentity, the school this provider sends as
  console.py          the default -- logs it, Sender ID included, and marks
                      it delivered
  bulksmsnigeria.py   the real gateway: POST /api/v2/sms, bearer token
  termii.py           stub: reads TERMII_API_KEY, builds the payload
  africastalking.py   stub: reads AFRICASTALKING_*, builds the payload
  __init__.py         PROVIDERS registry and get_provider()
```

Nothing above `providers/` imports a vendor. Who a message comes from is not the
provider's decision either — see [Each school sends under its own
name](#each-school-sends-under-its-own-name).

The console provider is not a mock — it is the default, deliberately. The whole
feature has to work end to end before anyone signs an SMS contract, and a demo
that dies on a missing API key demos nothing. Every screen, count and delivery
row you see running on it is the same code path a real gateway will drive. An
unknown `MESSAGING_PROVIDER` raises rather than falling back, because a school
that thinks it is sending real SMS must not quietly be writing to a log file.

Termii and Africa's Talking remain stubs, complete except for the HTTP call:
they read their settings, assemble the real payload (Termii wants
`2348031234567`, Africa's Talking wants `+2348031234567`), take their sender
from the school's identity like BulkSMS Nigeria does, and refuse to send without
credentials instead of returning a false success.

### Two models

```
Message  --<  MessageRecipient
```

**`Message`** — one press of Send: body, channel, audience description, how the
audience was chosen, sender, provider, a rollup status, and `sent_as` — the
Sender ID the batch actually went out under, snapshotted like the recipients'
phone numbers. A school that renames its Sender ID next term has not
retroactively sent last term's reminders under the new name.

**`MessageRecipient`** — one row per parent, with the phone number and parent
name *snapshotted at send time*, the per-recipient delivery status
(pending / sent / delivered / failed), the provider's reference and any error.
This is what makes the log real: a bursar needs to know that Mrs. Okonkwo's
number bounced, not merely that "JSS 1A parents were messaged".

`sent` and `delivered` are deliberately different states — a gateway accepting a
message says nothing about whether a handset received it.

`Message.status` is the one figure on the platform that is a stored rollup
rather than derived, so the log can be listed and filtered without a join. It is
never set by hand: `refresh_status()` recomputes it from the recipient rows, and
a delivery report arriving days later calls it again. The rows stay the source
of truth.

### Audiences resolve in one place

`apps/messaging/audiences.py` answers three questions that must never disagree —
the live count on the compose screen, the rows actually written, and the
description stored on the message. All three come out of one `resolve()` call,
so the number the bursar saw *is* the batch that went out.

Four filters: all parents at a campus, one class, hand-picked students, and
parents who owe. Students who are not active, students with no number on file,
and every other branch's parents are excluded — and a filter with nothing chosen
resolves to *nobody*, never to everybody.

### "Parents who owe" is the point

The core use case is fee reminders, so `/messaging/reminders/` is the compose
screen with that audience preset and a draft in the body. It selects students
whose confirmed payments do not cover their class's fee structure for the
current term — derived live, like every other fee figure here.

A class with no fee structure this term is **not** chased. Nobody has said what
it costs, so there is no balance, and texting those parents would be the
platform inventing a debt.

`apps/students/fees.py` gets those confirmed amounts from the payments app
through `_paid_amounts()`, which resolves it via the app registry rather than
importing it — so the roster keeps rendering whether or not payments is
installed. See [Payments](#payments).

### Screens

| URL | What it is |
| --- | --- |
| `/messaging/` | The log: audience, channel, when, and "32 sent, 30 delivered, 2 failed". |
| `/messaging/compose/` | Write, pick a channel and an audience, watch the count. |
| `/messaging/reminders/` | The same screen, preset to the parents who owe. |
| `/messaging/<id>/` | One batch, and what happened to every parent's copy. |
| `/messaging/recipients/count/` | JSON, for the live count. |

The live count is a small endpoint rather than a framework, and it runs the same
`resolve()` the send path runs. With scripting off, the count still renders on
load and the form still sends — the endpoint only saves a round trip.

### Each school sends under its own name

The platform (SCHOOLCORD) holds the account with the SMS gateway and pays for
the units. Each school registers its **own** alphanumeric Sender ID against it,
so a parent at Dap Group of Schools sees a message from `Dapgroup`, not from the
platform and not from another school.

**`SchoolMessagingConfig`** — one per school: the Sender ID, the gateway it is
registered with, an approval status, and optional credentials.

```
Platform:   SCHOOLCORD  (holds the BulkSMS Nigeria master account)
School:     Dap Group of Schools
Sender ID:  Dapgroup
Provider:   BulkSMS Nigeria
```

`api_key` and `account_reference` are nullable and blank for the pilot: the
master account sends on every school's behalf, under the school's own name. A
school that later takes out its own gateway account fills them in and the send
path does not change — `BulkSMSNigeriaProvider.api_token` already prefers the
school's key over the platform's.

`branch` is nullable and part of the uniqueness rule, so a school that later
wants a different Sender ID per campus can have one. A row with no branch is the
school's default; a row with one overrides it for that campus.
`apps/messaging/identity.py` is the only place that ordering is written down.

### Nothing sends under a name that is not the school's

A Sender ID is not usable until a gateway has approved it, so `resolve()` either
returns the school's identity or raises `SenderIdentityUnavailable` with a
sentence naming who fixes it:

* no config at all — "Dap Group of Schools has no Sender ID set up yet…"
* pending — "…is still awaiting approval, so nothing can be sent under it yet."
* rejected / suspended — the same, plus the note the platform recorded.

There is deliberately **no fallback**. Not the platform's name, not a blank
sender, and certainly not another school's: the first two get the batch rejected
by the gateway or ignored by parents, and the third is a tenancy breach a parent
would see on their own handset.

Resolution happens *before* anything is written, so a school that cannot send
gets no `Message` row at all — the log never shows a batch that was never really
sendable. The compose screen catches the same exception and keeps the draft.

### Which gateway carries it

Two decisions, not one:

| | Where it lives | What it means |
| --- | --- | --- |
| The school's gateway | `SchoolMessagingConfig.provider` | Where its Sender ID is registered. Two schools can be on two different gateways. |
| The platform override | `MESSAGING_PROVIDER` | When set, it wins over every school's choice. |

`MESSAGING_PROVIDER` defaults to `console`, so a fresh checkout sends nothing
anywhere. Clear it (`MESSAGING_PROVIDER=`) in production and each school goes out
through its own gateway. An override is not a fallback — it is the switch for
development, for tests, and for the day a gateway is down.

The Sender ID travels regardless: on the console provider the log line reads

```
[SMS] from Fulfilled [Fulfilled Academy] via BulkSMS Nigeria to 08118836702: ...
```

— the school's name, the gateway that *would* have carried it, and the message.
That is why per-tenant identity is testable with no credentials and no money
spent.

### BulkSMS Nigeria

`providers/bulksmsnigeria.py` is the real implementation, replacing the stub.
`POST /api/v2/sms` with a bearer token, the school's Sender ID as `from`, and
`dnd=2` so reminders still reach the many Nigerian numbers on the Do-Not-Disturb
register. It uses `urllib` from the standard library rather than pulling in
`requests`: one POST with a JSON body does not justify a dependency the school's
server then has to keep patched, and `urllib` gives us the timeout, which is the
part that actually matters when a gateway is slow.

Their API answers `200` for messages it then refuses, with the refusal in
`error`, so a 2xx is not on its own a success. And accepting a message means
**sent**, never **delivered** — whether a handset received it arrives later on
their webhook, and reporting it as delivered would make the log lie about the
one thing a bursar chasing a parent needs it for.

### The identity screen

`/messaging/identity/` serves two readers at different scopes. A school sees its
own Sender ID, the gateway, the approval status, and a preview of how it lands
on a parent's handset. The platform owner sees every school — **including the
ones with nothing registered**, since a roll that only lists the schools already
set up hides exactly the ones needing action — and registers or approves from
`/messaging/identity/<school>/edit/`.

| | View own identity | Register & approve |
| --- | --- | --- |
| Bursar | | |
| Principal | yes | |
| School owner | yes | |
| Platform owner | yes | yes |

Approving is platform-only because the platform is the party that actually
submits a Sender ID to the gateway. A school approving its own would be marking
its own homework, and the gateway would reject the first message anyway.

Two schools cannot register the same Sender ID: gateways do let unrelated
accounts hold similar names, but on one platform it means parents cannot tell
which school texted them, and it is almost always a copy-paste from the row
above.

### Seeding

```bash
python manage.py seed_messaging_identity          # Fulfilled Academy -> "Fulfilled"
python manage.py seed_messaging_identity --school "Dap Group of Schools" \
    --sender-id Dapgroup --provider bulksmsnigeria
python manage.py seed_messaging_identity --pending  # seed the blocked state
```

### Permissions

Bursar, principal and owner can all send. Messaging is the one place a bursar is
not read-only, and the reason is the job: chasing a fee is their work. Nothing in
messaging edits school data — it only reads the roster they can already see.

Nothing new is seeded; messaging runs off the existing students and their parent
contacts.

## Payments

At `/payments/`. The core of the product, and for the pilot it is **manual**:
the school keeps banking into its own account and the bursar records each
payment against a student. No gateway, no card, no settlement.

### One model

**`Payment`** — branch-owned, belongs to a student and a term. Amount, date
paid, label, method, reference, source, status, an optional receipt
attachment, and the audit trail (recorded / confirmed / voided, by whom and
when).

Two fields exist now specifically so a later change is not a rewrite:

* **`source`** — manual / gateway / bank-import. Every row is manual today.
  When a gateway or a bank-statement import starts creating rows, the ones a
  human typed stay distinguishable from the ones a machine reconciled, which is
  the first question anyone will ask when the two disagree.
* **`gateway_provider` / `gateway_reference`** — null on every row, waiting.

`term` is a foreign key rather than something worked out from the date: a
payment made in the holidays for next term is still that term's money, and only
the person recording it knows which.

### Balance is derived, never stored

```
expected = the class's FeeStructure total for the term
paid     = the sum of that student's CONFIRMED payments for that term
balance  = expected - paid
```

Not on the student, not on the term, not on the payment. It lives in
`apps/payments/balances.py` and is computed every time it is asked. Because
`paid_by_student()` is the single definition of "paid", confirming a receipt or
voiding a payment moves the student's balance, the outstanding list, the
bursar's dashboard and messaging's "parents who owe" in the same instant, with
no recalculation step anywhere.

Status derives too: **paid** (balance ≤ 0), **partial**, **unpaid**, and
**overdue** — which is not a fifth state but unpaid-or-partial past the term's
`due_date`. A term with no due date set never produces it: a deadline nobody
stated is not one a parent can have missed. The four existing payment-status
colours carry all of them.

An overpayment is a credit, not a negative balance.

### Only confirmed money counts

`pending` is the receipt handed in but not yet checked — the client's flow of
"someone uploads a receipt, the bursar confirms who and what it is for". A
pending payment sits in the queue, shows on the student's page, and changes no
balance at all until someone confirms it. The confirm step is the *same form*
as recording, deliberately: the bursar has to be able to correct the student,
the label and the amount against the slip at the moment they confirm.

### Labelling is descriptive

School Fees / Uniform / Books / Development / Other, set from the receipt or
from what the parent said. It does **not** split the balance — that stays one
combined figure. Per-label accounting would mean deciding what happens when a
parent pays 50,000 against a 30,000 uniform charge, and nobody has asked for
that answer.

### A voided payment is never deleted

It keeps its row, stops counting, and records who voided it and why. Voiding
asks for a reason and a tickbox, and the screen shows what the balance will
become before you press it. Money that was recorded and then reversed is a fact
about the account; erasing it is how a ledger stops being one.

Students are protected too: a student with payments against them cannot be
deleted from the roster — the screen says so and offers "withdraw instead",
which keeps the history and stops the billing.

### Screens

| URL | What it is |
| --- | --- |
| `/payments/` | Branch-wide list, filterable by label, status, method and date, with confirmed and pending totals for the filter. |
| `/payments/record/` | Record a payment. Student picked by typing; balance shown beside the amount. |
| `/payments/pending/` | The receipt queue: the slip alongside the figures it has to agree with. |
| `/payments/outstanding/` | Who owes, most owed first, with one click to message them. |
| `/payments/<id>/` | One payment, its attachment, its trail, and the balance it moved. |
| `/payments/<id>/receipt/` | A printable receipt with the running balance. |
| `/payments/<id>/void/` | Reverse it, with a reason. |
| `/payments/student/<id>/` | A student's full history. Their detail page shows the most recent eight. |

The student picker is a text input backed by a native `<datalist>`, not a long
`<select>` or a JS autocomplete: it is how the job is actually done — the bursar
has a receipt with an admission number on it and types it — and the browser's
own search matches a surname too. The receipt is plain HTML with a print
stylesheet rather than a generated PDF; the browser already knows how to print
and how to save as PDF, and a PDF library would be a dependency the school has
to keep working for a page that is one page long.

### Permissions

| | View | Record & confirm | Void |
| --- | --- | --- | --- |
| Bursar | yes | yes | |
| Principal | yes | yes | yes |
| School owner | yes | yes | yes |

Voiding is supervisory: it reverses confirmed money. If the school would rather
the bursar could undo their own mistakes, add `VOID_PAYMENTS` to `_BURSAR` in
`apps/core/permissions.py` — the screens follow the capability table and nothing
else changes.

### Seeding

```bash
python manage.py seed_payments            # needs seed_students to have run
python manage.py seed_payments --replace  # clear this term's payments and reseed
```

The spread is chosen so every state is actually on screen: students paid in
full (in one payment and in two instalments), part paid, untouched, two pending
receipts with attachments, and one voided payment with its reason. Amounts come
from each student's own class fee structure rather than being hard-coded, so
re-pricing a class re-seeds sensible figures.

**Overdue is the one status the seed does not produce, and cannot.** It is a
property of the *term*, not of a student: the moment a term's `due_date` is in
the past, every unpaid and part-paid balance at that branch turns overdue at
once. Seeding one would therefore hide the unpaid and part-paid states behind a
wall of red rather than adding a fourth colour beside them. Set a due date on
the term (Django admin → Terms) and the overdue pill appears everywhere it
should. The behaviour itself is covered by tests in
`apps/payments/tests.py::OverdueTests`.

## The public side and signing in

Everything above assumed you were already signed in. This is how you get there.

| URL | What it is |
| --- | --- |
| `/` | Landing page. Public; a signed-in visitor is redirected to their dashboard. |
| `/signup/` | Creates a School, its first Branch and the owner User, then signs them in. |
| `/welcome/` | The four-step onboarding checklist. |
| `/accounts/login/` | Login, landing each role on its own dashboard. |
| `/accounts/password_reset/` | Django's reset flow; the console backend prints the link in dev. |
| `/dashboard/` | Not a screen — forwards to whichever dashboard the role belongs on. |

### One dashboard per role

"The dashboard" is a different question for each role, so there are four
screens rather than one with three quarters hidden:

| Role | Lands on | Answers |
| --- | --- | --- |
| Platform owner | `/platform/` | Every school, its plan, its size. |
| School owner | `/school/` | Every campus they run, side by side. |
| Principal | `/branch/` | Their campus: enrolment, classes, what is unpriced. |
| Bursar | `/finance/` | What the term is worth, per class. |

`ROLE_HOME` in `apps/core/navigation.py` is the single source for that mapping.
Both the post-login redirect and the sidebar's "Dashboard" entry read it, so
the link in the shell and the page you land on after signing in cannot drift
apart — a test asserts they agree for every role. `next=` still wins when
present, so a deep link survives the login page.

Reaching another role's dashboard by typing its URL is not a leak — the scoped
managers narrow it either way — but it is a screen answering someone else's
question, so `RoleDashboardMixin` sends you to your own instead.

The effective role comes from `TenantContext.from_user`, not `user.role`, which
is what sends a Django superuser to the platform overview rather than to
whatever role happens to be stored on their row.

### Signup is the one place without a tenant

There is no tenant yet, so the scoped managers would return nothing and a
uniqueness check that cannot see existing rows is not a uniqueness check.
`SchoolSignupForm` therefore uses `all_objects` throughout, and creates the
school, the branch and the owner in one transaction — a signup that produced a
School with no owner would leave a tenant nobody can sign into and no screen
from which to fix it.

The owner is created with **no branch**: they see every campus, and pinning them
to the first one would narrow them the day they open a second. The username is
derived from the email (they never type one), with a numeric suffix on collision
rather than a rejected signup.

### Onboarding progress is derived

`views.setup_progress()` asks the data — are there classes, fee structures,
students, colleagues? — rather than reading a "setup complete" flag. A flag
would go stale the moment someone deleted their last class, and the school would
be told it had finished a step it had not.

### Trying it

```bash
python manage.py bootstrap_tenant --name "Fulfilled Academy"
```

Then sign in as `fulfilled-academy.owner`, `.principal`, `.bursar` or
`platform.owner`, password `dependable`. Each account is given an
`@example.com` address so the password-reset flow has somewhere to send; in dev
the console backend prints the whole message, link included.

## Branding and identity

### One name, one file

`apps/core/branding.py` holds `PRODUCT_NAME` and nothing else does. Settings
imports it for `DEFAULT_FROM_EMAIL`, `config/urls.py` for the admin header, and
a context processor puts it on every page — including the signed-out ones,
which is why it is a separate processor from `core.context_processors.tenancy`:
that one returns early for anonymous visitors, and the landing page and login
form are exactly where the product's name matters most.

The password-reset **email** is the one surface a context processor cannot
reach. Django renders it from the form, with a plain context and no request, so
`{{ product_name }}` there would silently render as nothing and ship "Reset
your  password" to a customer. `BrandedPasswordResetView` hands it in through
`extra_email_context`, and a test asserts the subject has no double space in it.

### The comment bug this pass fixed

Django's `{# ... #}` is **single-line only**. A comment that wraps onto a second
line is not a comment at all — the lexer never recognises it, and the browser
prints the developer's prose to the user. Twelve templates had one; the login
page was telling every visitor what Django does with a bad password.

Nothing fails when this happens: the template is valid, the page renders, the
tests pass. So there are two guards, and they work differently on purpose:

* `TemplateCommentSyntaxTests` reads every template's **source** and fails with
  the file and line number of any `{#` whose `#}` is on another line;
* `RenderedPageTests` reads the **rendered HTML** of every public page and
  fails on any `{#`, `#}`, `{%`, old brand name, `TODO` or lorem ipsum.

Multi-line comments belong in `{% comment %}...{% endcomment %}`, which is what
all twelve became.

### Logos

`School.logo` is an optional upload; without one, a school renders as its
initial in a coloured square — the same pattern the roster already uses for
students. That fallback is the answer, not a placeholder: most schools will
never upload a logo, and an initial square looks deliberate where a broken
image does not.

| Partial | What it draws |
| --- | --- |
| `partials/_brand_mark.html` | SCHOOLCORD's own mark. Inline SVG, so it cannot 404 and inherits `currentColor` for the dark sidebar and the white auth card alike. |
| `partials/_school_logo.html` | A tenant's logo, or its initial square. |

The product's mark appears on the landing page, login, signup, password reset,
the sidebar and the platform dashboard. A school's own logo appears in its
sidebar, on the platform roll, and on printed payment receipts — where the
print stylesheet forces `print-color-adjust` so the fallback square is not
dropped as a background colour.

It is a `FileField` with an image validator rather than an `ImageField`, which
would pull in Pillow to read a header. The receipt upload on `payments.Payment`
made the same trade.

Upload it at **Settings** (`/settings/`), which is also what the sidebar's
Settings entry has been pointing at since the shell was built. The onboarding
page prompts for it from the school card at the top.

The school profile is deliberately **not** an onboarding checklist step: signup
already sets the name, and a logo is optional, so any "done" test would either
tick itself the moment the account existed or could never be ticked at all.

### The admin

Lightly skinned in `templates/admin/base_site.html` — brand blue through
Django's own theme variables, the product name from `site_header`, and the
house 8px corner. Not a rebuild: it is an internal tool, and the hex values are
literal because the admin does not load `app.css`.

### Demo data is obviously demo

The genuine pilot tenant — **Fulfilled Academy**, its roster, its pricing — is
untouched; it represents real pilot data. What changed is everything that was
*filler* dressed as real:

* `bootstrap_tenant` now creates **"Demo School"**, not "Northgate Academy".
* The signup form's example is "Bright Future Academy", not the pilot school's
  actual name.
* Student and parent form placeholders are role words ("Surname", "Parent or
  guardian name") rather than invented people.
* **Every demo parent phone number moved to a sequential `0800` block.** They
  were valid eleven-digit numbers on live Nigerian mobile prefixes — point
  messaging at a real gateway with the seed loaded and fee reminders go to
  strangers. `0800` is a Nigerian toll-free service range, not a handset range:
  it satisfies the phone validator, reads as obviously invented, and cannot be
  delivered to. A test asserts every one of them starts `0800`.

Parent emails were already on `example.com`, which is reserved for this.

## Navigation

`nav_for(role)` in `apps/core/navigation.py` is the server-side equivalent of a
client `navFor(role)`. A context processor injects the result, and
`templates/partials/_nav.html` renders it for both the desktop sidebar and the
mobile drawer. Links the role may not use are never emitted; destinations that
don't exist yet render greyed out with a "soon" tag and light up on their own
once the URL name exists.

## Design tokens

All declared in `static/src/app.css` under `@theme` (Tailwind v4), so each token
is both a utility and a CSS custom property.

- **Brand** — deep blue, `brand-50` … `brand-950` (`brand-900` = `#0f2547`).
- **Payment status** — `paid` (green), `partial` (amber), `unpaid` (slate),
  `overdue` (red), each with `-soft` and `-strong` variants. Use the
  `status-pill status-paid` pattern: colour is always paired with a label, never
  the only signal.
- **Type** — Public Sans for UI, IBM Plex Mono for money and IDs (`.numeric`
  adds tabular figures).
- **Radii** — 8px (`rounded-md` / `rounded-lg`).
- **Touch targets** — 44px minimum via `--spacing-touch` (`min-h-touch`,
  `size-touch`), applied to every button, input and nav link.

Components (`.btn`, `.card`, `.field-input`, `.nav-link`, `.status-pill`) compose
in markup — `class="btn btn-primary"` — because Tailwind v4's `@apply` only
accepts real utilities.

## Layout

`templates/base.html` is the shell every role dashboard extends: permanent
sidebar from `lg` up, off-canvas drawer below it, sticky header, skip link,
dependency-free JS. Signed-out pages fill `{% block anonymous_content %}` and
skip the shell entirely.

```
{% extends "base.html" %}
{% block page_title %}Fees{% endblock %}
{% block content %}...{% endblock %}
```

## Admin

Schools, branches and users are editable at `/admin/` immediately.
`TenantScopedAdminMixin` narrows both the changelists and the foreign-key
dropdowns, so a school owner cannot see — or reassign a row to — another tenant.
Django superusers bypass it.

## Layout of the code

```
config/settings/     base / dev / prod, plus the database fallback logic
apps/core/           branding.py (the product's one name), tenancy.py,
                     permissions.py, models.py, roles.py,
                     navigation.py (incl. ROLE_HOME), middleware.py, admin.py,
                     forms.py (incl. SchoolSignupForm), views.py (landing,
                     signup, onboarding, the four role dashboards)
apps/schools/        School (the tenant) and Branch
apps/accounts/       the custom User
apps/academics/      Class and Subject, their screens, and curriculum.py
apps/fees/           Term, FeeStructure, FeeComponent, screens, and pricing.py
apps/students/       Student, its screens, the derived fee position (fees.py),
                     validators.py, roster.py, and the Excel import
                     (importer.py validates, workbook.py reads and writes .xlsx)
apps/messaging/      SchoolMessagingConfig (each school's Sender ID),
                     Message and MessageRecipient, audiences.py (who a filter
                     means), identity.py (who a message is from),
                     dispatch.py (record then deliver), validators.py, and
                     providers/ (the interface, console, BulkSMS Nigeria,
                     Termii, Africa's Talking)
apps/payments/       Payment, balances.py (every derived figure), its screens,
                     and seed_payments
templates/           base.html, 403.html, partials/, core/ (landing, signup,
                     onboarding, dashboards), academics/, fees/, students/,
                     messaging/, payments/, registration/ (_auth_base.html plus
                     login, password reset and password change)
static/src/app.css   design tokens and components (Tailwind source)
static/css/app.css   compiled output (committed)
```
