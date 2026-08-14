# Dependable

Multi-tenant school management SaaS.

- **Step 1 — foundation:** tenancy, a custom user model, the query-scoping
  pattern, the design-system shell, and the admin.
- **Step 2 — academic setup:** classes and subjects per branch, with management
  screens.

Fees, students and payments are deliberately absent — those are the next branches.

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

62 tests, covering the parts that must never regress: what each role can see,
and what each role may change. The negative-path tests deliberately trigger 403s
and 404s, so Django logs tracebacks during a passing run.

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

class Student(TenantScopedModel):     # brings school + branch FKs and indexes
    full_name = models.CharField(max_length=200)
```

Use `BranchScopedModel` instead when the record must live at one campus, as
`Class` and `Subject` do. It makes `branch` required and derives `school` from
it, so a platform owner — who has no school of their own — can still create rows.

You get:

- `school` and `branch` foreign keys (`branch` nullable — some records belong to
  the school as a whole).
- `Student.objects` — filtered by the active tenant automatically.
- `Student.all_objects` — never filtered, for jobs and cross-tenant reporting.
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
templates/           base.html, 403.html, partials/, core/, academics/,
                     registration/
static/src/app.css   design tokens and components (Tailwind source)
static/css/app.css   compiled output (committed)
```
