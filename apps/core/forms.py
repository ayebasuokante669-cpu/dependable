"""Form helpers that keep hand-written forms on the design system."""

from __future__ import annotations

from django import forms


class StyledFormMixin:
    """Apply the design-system input classes to every widget on a form.

    Saves repeating ``widget=forms.TextInput(attrs={"class": "field-input"})``
    on every field, and means a token change lands everywhere at once.
    ``setdefault`` is used so a field can still opt out by setting its own class.
    """

    #: Widgets that must not receive the text-input styling.
    _UNSTYLED = (
        forms.CheckboxInput,
        forms.CheckboxSelectMultiple,
        forms.RadioSelect,
        forms.FileInput,
        forms.HiddenInput,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, self._UNSTYLED):
                if isinstance(widget, forms.CheckboxInput):
                    widget.attrs.setdefault("class", "field-checkbox")
                continue
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("class", "field-textarea")
                widget.attrs.setdefault("rows", 3)
            else:
                widget.attrs.setdefault("class", "field-input")
