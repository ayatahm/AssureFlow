from django import forms
from django.contrib.auth import get_user_model

from apps.accounts.models import Department, Role
from apps.content.models import ScopeTag
from apps.reviews.models import Answer, RequestType, Review, TechnicalResult

User = get_user_model()


def users_with_role(role):
    return (
        User.objects.filter(is_active=True, is_superuser=False, groups__name=role)
        .exclude(groups__name=Role.ADMIN)
        .distinct()
        .order_by("full_name")
    )


class IntakeForm(forms.ModelForm):
    request_type = forms.ChoiceField(choices=RequestType.choices, widget=forms.RadioSelect)
    scope_tags = forms.ModelMultipleChoiceField(
        queryset=ScopeTag.objects.all(), required=False, widget=forms.CheckboxSelectMultiple,
        label="Proposed technical scope",
    )
    assigned_reviewer = forms.ModelChoiceField(queryset=User.objects.none(), label="Assigned Reviewer")
    requester = forms.ModelChoiceField(
        queryset=User.objects.none(), required=False, label="Requester (when entering on behalf of a department)"
    )

    class Meta:
        model = Review
        fields = ("title", "request_type", "department", "project_owner", "target_date", "purpose", "goals",
                  "external_ref")
        widgets = {
            "target_date": forms.DateInput(attrs={"type": "date"}),
            "purpose": forms.Textarea(attrs={"rows": 3}),
            "goals": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {"title": "Review title"}

    def __init__(self, *args, actor=None, creating=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.filter(is_active=True)
        if creating:
            self.fields["assigned_reviewer"].queryset = users_with_role(Role.REVIEWER)
            if actor is not None and actor.has_role(Role.REVIEWER):
                self.fields["assigned_reviewer"].initial = actor.pk
                self.fields["requester"].queryset = users_with_role(Role.REQUESTER).exclude(pk=actor.pk)
                self.fields["requester"].required = not actor.has_role(Role.REQUESTER)
            else:
                del self.fields["requester"]
            if actor is not None and actor.department_id:
                self.fields["department"].initial = actor.department_id
        else:
            del self.fields["assigned_reviewer"]
            del self.fields["requester"]
            if self.instance.pk:
                self.fields["scope_tags"].initial = list(
                    self.instance.scope_tags.values_list("scope_tag_id", flat=True)
                )


class TriageForm(forms.Form):
    """Dynamic fields built from the loaded rule set. No default answers."""

    def __init__(self, *args, questions=(), initial_answers=None, disabled=False, **kwargs):
        super().__init__(*args, **kwargs)
        initial_answers = initial_answers or {}
        self.questions = list(questions)
        for q in self.questions:
            if q.kind == "YES_NO":
                choices = [("YES", "Yes"), ("NO", "No")]
            else:
                choices = [(o, o.title()) for o in q.options]
            self.fields[q.code] = forms.ChoiceField(
                label=q.text, help_text=q.help_text, choices=choices, required=False,
                widget=forms.RadioSelect, initial=initial_answers.get(q.code), disabled=disabled,
            )


class AnswerForm(forms.Form):
    answer = forms.ChoiceField(choices=Answer.choices, widget=forms.RadioSelect)
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))


class ScopeConfirmForm(forms.Form):
    tags = forms.ModelMultipleChoiceField(
        queryset=ScopeTag.objects.all(), required=False, widget=forms.CheckboxSelectMultiple, label="Confirmed scope"
    )
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        label="Confirmation note (required if the scope is empty)",
    )


class TechnicalItemForm(forms.Form):
    included = forms.BooleanField(required=False, label="Included in scope")
    exclusion_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    result = forms.ChoiceField(choices=TechnicalResult.choices, required=False, label="Recorded result (optional)")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class AddTechnicalItemForm(forms.Form):
    control = forms.ModelChoiceField(queryset=None, label="Catalogue item")

    def __init__(self, *args, review=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.content.models import TechnicalControl

        present = review.technical_items.filter(included=True).values_list("control_id", flat=True)
        self.fields["control"].queryset = TechnicalControl.objects.filter(
            catalogue=review.technical_catalogue
        ).exclude(pk__in=present)
