"""Forms for academic setup.

Every queryset here goes through a tenant-scoped manager, so the choices a user
is offered are already limited to what they may see. The extra validation below
guards the one thing scoping alone cannot: a school owner who *can* see two
branches must still not wire them together.
"""

from __future__ import annotations

from django import forms
from django.forms import BaseModelFormSet, modelformset_factory

# BranchScopedForm lives in core: fees needs the same behaviour.
from apps.core.forms import BranchScopedForm, StyledFormMixin
from apps.schools.models import Branch

from .curriculum import infer_level, infer_year
from .models import Class, Level, Subject


class ClassForm(BranchScopedForm):
    class Meta:
        model = Class
        fields = ["branch", "name", "level", "year_in_level", "stream", "is_active"]

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_stream(self):
        return self.cleaned_data["stream"].strip()


class SubjectForm(BranchScopedForm):
    classes = forms.ModelMultipleChoiceField(
        queryset=Class.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Taught in",
        help_text="Tick every class this subject is taught in.",
    )

    class Meta:
        model = Subject
        fields = ["branch", "name", "code", "classes", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scoped manager -> only classes this user may see are even offered.
        self.fields["classes"].queryset = Class.objects.filter(is_active=True)

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean(self):
        cleaned = super().clean()
        branch = cleaned.get("branch")
        classes = cleaned.get("classes")
        if branch and classes:
            stray = [c.display_name for c in classes if c.branch_id != branch.pk]
            if stray:
                raise forms.ValidationError(
                    {
                        "classes": "These classes belong to a different branch: "
                        + ", ".join(stray)
                    }
                )
        return cleaned


# ---------------------------------------------------------------------------
# Bulk entry
#
# A school sets up around nineteen classes, and used to do it nineteen times:
# open the form, fill five fields, save, land back on the list, press New
# again. The forms below are the same records entered as a table -- one campus
# chosen once at the top, then a row per class, added without a round trip.
#
# Nothing about what gets saved changes. These are ordinary ModelForms over
# the same models with the same constraints; they are just arranged so that
# entering the second one costs a keystroke instead of a page load.
# ---------------------------------------------------------------------------


class BranchChoiceForm(StyledFormMixin, forms.Form):
    """Which campus a whole batch belongs to.

    Asked once per batch rather than once per row: a school adding its class
    ladder is adding it to one campus, and repeating the answer nineteen times
    is exactly the back-and-forth this screen exists to remove.

    The queryset is the tenant-scoped manager's, so the campuses offered are
    already only the ones this user may see.
    """

    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        label="Campus",
        help_text="Everything added below is created at this campus.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        branches = Branch.objects.filter(is_active=True)
        self.fields["branch"].queryset = branches
        self.fields["branch"].empty_label = None
        if len(branches) == 1:
            self.fields["branch"].initial = branches[0]

    @property
    def is_forced(self) -> bool:
        """True when there is only one campus to choose.

        The template hides the field rather than presenting a select with a
        single option -- a question with one possible answer is not a question.
        """
        return len(self.fields["branch"].queryset) == 1


class ClassRowForm(StyledFormMixin, forms.ModelForm):
    """One row of the bulk class table.

    ``level`` and ``year_in_level`` are optional here, unlike on the single
    form, because both can usually be read off the name -- "JSS 2" is the
    junior band, year two. See :func:`apps.academics.curriculum.infer_level`.
    The guess is filled into ``cleaned_data`` before validation finishes, so
    what is saved is a fully specified class either way; it simply did not
    have to be typed.
    """

    class Meta:
        model = Class
        fields = ["name", "level", "year_in_level", "stream"]
        widgets = {
            "name": forms.TextInput(
                attrs={"placeholder": "e.g. JSS 1", "autocomplete": "off"}
            ),
            "stream": forms.TextInput(
                attrs={"placeholder": "Optional", "autocomplete": "off"}
            ),
            "year_in_level": forms.NumberInput(
                attrs={"min": 0, "max": 20, "inputmode": "numeric",
                       "placeholder": "Auto"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Both are inferred from the name when left blank, so neither may be
        # required at the field level. `clean()` fills them in and raises if it
        # genuinely cannot work one out.
        self.fields["level"].required = False
        # `level` is a TypedChoiceField, not a ModelChoiceField, so the blank
        # option is relabelled through `choices` rather than `empty_label`.
        # "From the name" says what leaving it alone will actually do.
        self.fields["level"].choices = [("", "From the name"), *Level.choices]
        self.fields["year_in_level"].required = False
        self.fields["year_in_level"].help_text = ""
        self.fields["name"].help_text = ""
        self.fields["stream"].help_text = ""
        self.fields["stream"].label = "Stream"

    def has_changed(self):
        """A row counts as filled only when a name was typed into it.

        Django's default compares every field against its initial, which is
        the wrong question on a table of blank rows: `year_in_level` is
        pre-filled with 1, so a row where somebody cleared that field counts as
        "changed" and is then rejected for having no name -- a row they were
        plainly not using. The name is the one field that says whether this row
        is meant to exist at all.

        Returning False here makes `empty_permitted` skip the row entirely, so
        a half-touched blank row is ignored rather than turned into an error.
        """
        return bool((self.data.get(self.add_prefix("name")) or "").strip())

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean_stream(self):
        return self.cleaned_data.get("stream", "").strip()

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get("name")
        if not name:
            # A row with no name is a blank row. `has_changed()` decides
            # whether it is skipped or reported; there is nothing to infer.
            return cleaned

        if cleaned.get("level") in (None, ""):
            guessed = infer_level(name)
            if guessed is None:
                self.add_error(
                    "level",
                    "Could not tell which band this is from the name — pick one.",
                )
            else:
                cleaned["level"] = guessed

        if cleaned.get("year_in_level") in (None, ""):
            cleaned["year_in_level"] = infer_year(name) or 1

        return cleaned


class BaseClassRowFormSet(BaseModelFormSet):
    """Validates the batch as a batch.

    Two things no single row can check for itself: whether two rows in the
    same submission name the same class, and whether a row collides with a
    class the branch already has. Both are the database's unique constraint
    (branch, name, stream) -- caught here so the person is told which row is
    the problem rather than being shown an IntegrityError.
    """

    def __init__(self, *args, branch=None, **kwargs):
        self.branch = branch
        super().__init__(*args, **kwargs)

    @staticmethod
    def _key(name: str, stream: str) -> tuple[str, str]:
        return (name.casefold(), (stream or "").casefold())

    def validate_unique(self):
        """Deliberately a no-op; :meth:`clean` does this job better.

        Django's own version compares rows against each other and reports a
        generic non-form error. It cannot see the branch -- which is not a
        field on these rows -- so it misses collisions with classes the campus
        already has, and it cannot say which row is the problem. `clean()`
        checks both and attaches the error to the offending row's name field.
        """
        return

    def filled_forms(self) -> list:
        """The rows that were actually typed into."""
        return [
            form
            for form in self.forms
            if form.cleaned_data.get("name")
        ]

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        filled = self.filled_forms()
        if not filled:
            raise forms.ValidationError("Add at least one class before saving.")

        # Read every key up front. `add_error` deletes the field it names from
        # that form's `cleaned_data`, so a second pass over `filled` reading
        # `cleaned_data["name"]` would raise KeyError on any row the first pass
        # has already flagged.
        keyed = [
            (form, self._key(form.cleaned_data["name"],
                             form.cleaned_data.get("stream", "")))
            for form in filled
        ]

        seen: dict[tuple[str, str], int] = {}
        for position, (form, key) in enumerate(keyed, start=1):
            if key in seen:
                form.add_error(
                    "name",
                    f"Same class as row {seen[key]} above — remove one of them.",
                )
            else:
                seen[key] = position

        if self.branch is None:
            return

        # One query for the whole batch rather than one per row.
        existing = {
            self._key(name, stream)
            for name, stream in Class.all_objects.filter(
                branch=self.branch
            ).values_list("name", "stream")
        }
        for form, key in keyed:
            if key in existing:
                form.add_error(
                    "name", "This campus already has a class with this name."
                )


#: Five blank rows to begin with -- enough to start typing into without the
#: script, and the "add another" button supplies the rest. `extra` is ignored
#: once the formset is bound, so a failed submit does not grow the table.
ClassRowFormSet = modelformset_factory(
    Class,
    form=ClassRowForm,
    formset=BaseClassRowFormSet,
    extra=5,
    can_delete=False,
)


class ClassEditRowForm(ClassRowForm):
    """One row of the edit-them-all table.

    The add form plus ``is_active``, which is the field this screen exists for
    alongside renaming: a class that stopped running is deactivated rather than
    deleted, because deleting it would take its fee structures and its students'
    history with it.
    """

    class Meta(ClassRowForm.Meta):
        fields = ["name", "level", "year_in_level", "stream", "is_active"]

    def has_changed(self):
        """Django's own comparison, not the add form's.

        ``ClassRowForm`` treats "a name was typed" as changed, which is the
        right question for a blank row on the add screen and the wrong one
        here: every existing row has a name, so that rule would mark all
        nineteen dirty and re-save each of them on every submit. Skipping one
        level up restores the field-by-field comparison, which is what decides
        who actually needs an UPDATE.
        """
        return super(ClassRowForm, self).has_changed()


class BaseClassEditFormSet(BaseModelFormSet):
    """Keeps the batch's names unique, against each other and against the rest.

    The branch is not posted here -- each row already belongs to one -- so the
    check is per branch of the row being edited, which is also what lets an
    owner editing two campuses at once rename "JSS 1" at North without being
    told it collides with the "JSS 1" at South.
    """

    def validate_unique(self):
        """See BaseClassRowFormSet.validate_unique -- same reasoning."""
        return

    @staticmethod
    def _key(branch_id, name: str, stream: str) -> tuple:
        return (branch_id, name.casefold(), (stream or "").casefold())

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        rows = [form for form in self.forms if form.cleaned_data.get("name")]
        keyed = [
            (
                form,
                self._key(
                    form.instance.branch_id,
                    form.cleaned_data["name"],
                    form.cleaned_data.get("stream", ""),
                ),
            )
            for form in rows
        ]

        seen: dict[tuple, int] = {}
        for position, (form, key) in enumerate(keyed, start=1):
            if key in seen:
                form.add_error(
                    "name",
                    f"Same class as row {seen[key]} above — give one of them a "
                    f"different name or stream.",
                )
            else:
                seen[key] = position

        # Classes at the same campuses that are *not* on this screen. Editing
        # one row into the name of a row that was filtered out would otherwise
        # only fail at the database.
        editing = {form.instance.pk for form in self.forms if form.instance.pk}
        branch_ids = {form.instance.branch_id for form in rows}
        elsewhere = {
            self._key(branch_id, name, stream)
            for branch_id, name, stream in Class.all_objects.filter(
                branch_id__in=branch_ids
            )
            .exclude(pk__in=editing)
            .values_list("branch_id", "name", "stream")
        }
        for form, key in keyed:
            if key in elsewhere:
                form.add_error(
                    "name", "This campus already has a class with this name."
                )


#: No `extra`: this screen edits what exists. Adding is the bulk-add screen's
#: job, and mixing the two would mean a table whose blank rows at the bottom
#: behave differently from the rows above them.
ClassEditFormSet = modelformset_factory(
    Class,
    form=ClassEditRowForm,
    formset=BaseClassEditFormSet,
    extra=0,
    can_delete=False,
)


class SubjectRowForm(StyledFormMixin, forms.ModelForm):
    """One row of the bulk subject table. Name and optional short code."""

    class Meta:
        model = Subject
        fields = ["name", "code"]
        widgets = {
            "name": forms.TextInput(
                attrs={"placeholder": "e.g. Mathematics", "autocomplete": "off"}
            ),
            "code": forms.TextInput(
                attrs={"placeholder": "MTH", "autocomplete": "off"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].help_text = ""
        self.fields["name"].help_text = ""

    def has_changed(self):
        """See ClassRowForm.has_changed -- the name decides, not the code."""
        return bool((self.data.get(self.add_prefix("name")) or "").strip())

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean_code(self):
        return self.cleaned_data.get("code", "").strip().upper()


class BaseSubjectRowFormSet(BaseModelFormSet):
    """The subject batch's own duplicate checks. See BaseClassRowFormSet."""

    def __init__(self, *args, branch=None, **kwargs):
        self.branch = branch
        super().__init__(*args, **kwargs)

    def validate_unique(self):
        """See BaseClassRowFormSet.validate_unique -- same reasoning."""
        return

    def filled_forms(self) -> list:
        return [form for form in self.forms if form.cleaned_data.get("name")]

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        filled = self.filled_forms()
        if not filled:
            raise forms.ValidationError("Add at least one subject before saving.")

        # Keys read up front: `add_error` removes the named field from that
        # form's cleaned_data, so a second pass would KeyError on a flagged row.
        keyed = [(form, form.cleaned_data["name"].casefold()) for form in filled]

        seen: dict[str, int] = {}
        for position, (form, key) in enumerate(keyed, start=1):
            if key in seen:
                form.add_error(
                    "name",
                    f"Same subject as row {seen[key]} above — remove one of them.",
                )
            else:
                seen[key] = position

        if self.branch is None:
            return

        existing = {
            name.casefold()
            for name in Subject.all_objects.filter(
                branch=self.branch
            ).values_list("name", flat=True)
        }
        for form, key in keyed:
            if key in existing:
                form.add_error(
                    "name", "This campus already teaches a subject with this name."
                )


SubjectRowFormSet = modelformset_factory(
    Subject,
    form=SubjectRowForm,
    formset=BaseSubjectRowFormSet,
    extra=6,
    can_delete=False,
)


class SubjectBatchClassesForm(StyledFormMixin, forms.Form):
    """The classes every subject in one batch is attached to.

    Asked once for the batch rather than per row, and optional. It exists
    because the batches people actually add are level-shaped -- "the primary
    set", "the junior secondary set" -- and ticking the same six classes
    against each of nine subjects is the same round trip in a different shape.

    A subject taught somewhere else as well is edited afterwards on its own
    form, which is the right place for an exception.
    """

    classes = forms.ModelMultipleChoiceField(
        queryset=Class.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Taught in",
        help_text="Optional. Every subject added below is attached to the "
                  "classes ticked here.",
    )

    def __init__(self, *args, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        classes = Class.objects.filter(is_active=True).select_related("branch")
        if branch is not None:
            classes = classes.filter(branch=branch)
        self.fields["classes"].queryset = classes

        # A school owner sees several campuses at once, so a bare class name is
        # ambiguous. A principal sees one and does not need the noise.
        if Branch.objects.count() > 1 and branch is None:
            self.fields["classes"].label_from_instance = (
                lambda obj: f"{obj.branch.name} — {obj.display_name}"
            )
        else:
            self.fields["classes"].label_from_instance = (
                lambda obj: obj.display_name
            )
