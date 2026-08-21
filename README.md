
# Dependable

Multi-tenant school management SaaS.

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

Payments are deliberately absent — that is the next branch.

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
apps/core/           tenancy.py, permissions.py, models.py, roles.py,
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
templates/           base.html, 403.html, partials/, core/ (landing, signup,
                     onboarding, dashboards), academics/, fees/, students/,
                     registration/ (_auth_base.html plus login, password reset
                     and password change)
static/src/app.css   design tokens and components (Tailwind source)
static/css/app.css   compiled output (committed)
```
