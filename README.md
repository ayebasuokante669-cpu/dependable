# Dependable

Multi-tenant school management SaaS.

- **Step 1 — foundation:** tenancy, a custom user model, the query-scoping
  pattern, the design-system shell, and the admin.
- **Step 2 — academic setup:** classes and subjects per branch, with management
  screens.
- **Step 3 — fee structures:** terms, and what each class owes per term.
- **Step 4 — student records:** the roster, entered by hand, with each student's
  fee position derived from their class.

The Excel import and payments are deliberately absent — those are the next
branches.

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

180 tests, covering the parts that must never regress: what each role can see,
what each role may change, that a fee total always equals its live line items,
and that a student's expected fee is always read from their class rather than
stored on them. The negative-path tests deliberately trigger 403s and 404s, so
Django logs tracebacks during a passing run.

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
spans several. Mathematics is *one* row linked to Primary 1 through SSS 3, not
eleven near-duplicates.

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
classes (13 single-arm plus three senior years × Arts/Science) and 21 subjects.

Twenty-one, not twenty-eight, because a subject taught at several levels is one
row: Religion Studies spans all 19 classes, Mathematics spans 15. Where the name
differs by level it stays a separate subject — "Social Studies" (primary) and
"Social & Citizenship Studies" (secondary), "History" and "Nigeria History".

| Level | Subjects |
| ----- | -------: |
| Nursery | 8 |
| Primary | 10 |
| Junior Secondary | 10 |
| Senior Secondary | 10 |

The secondary list is one set covering JSS and SSS, so **SSS Arts and SSS Science
currently carry identical subject lists**. The arms exist as classes and the
seeder already supports narrowing — change a placement to `at(SENIOR, "Science")`
when a subject should belong to one arm only.

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
arrives, which matters for the Excel import next.

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

Parent phone numbers are validated as Nigerian mobiles and stored in one
canonical form (`08034129876`), whichever of `0803 412 4567`, `+234 803 …` or
`234 …` was typed; the screens group them for reading and link them for calling.

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
                     navigation.py, middleware.py, forms.py, admin.py, tests.py
apps/schools/        School (the tenant) and Branch
apps/accounts/       the custom User
apps/academics/      Class and Subject, their screens, and curriculum.py
apps/fees/           Term, FeeStructure, FeeComponent, screens, and pricing.py
apps/students/       Student, its screens, the derived fee position (fees.py),
                     validators.py, and roster.py
templates/           base.html, 403.html, partials/, core/, academics/, fees/,
                     students/, registration/
static/src/app.css   design tokens and components (Tailwind source)
static/css/app.css   compiled output (committed)
```
