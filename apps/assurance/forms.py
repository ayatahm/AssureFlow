from django import forms

from apps.accounts.models import Role
from apps.reviews.forms import users_with_role

from .models import AcceptanceState, ComplianceDecisionValue, ConclusionStatus, Priority, RiskRating


class EvidenceUploadForm(forms.Form):
    file = forms.FileField(label="Evidence file")
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class ConclusionForm(forms.Form):
    status = forms.ChoiceField(choices=ConclusionStatus.choices, widget=forms.RadioSelect)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class FindingForm(forms.Form):
    title = forms.CharField(max_length=200)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    priority = forms.ChoiceField(choices=Priority.choices)
    owner = forms.ModelChoiceField(queryset=None, label="Action owner")
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    expected_outcome = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, review=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = users_with_role(Role.REQUESTER).filter(
            review_assignments__review=review, review_assignments__role="REQUESTER"
        )


class ActionForm(forms.Form):
    owner = forms.ModelChoiceField(queryset=None, label="Action owner")
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    expected_outcome = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, review=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = users_with_role(Role.REQUESTER).filter(
            review_assignments__review=review, review_assignments__role="REQUESTER"
        )


class ActionSubmitForm(forms.Form):
    note = forms.CharField(label="Remediation completed", widget=forms.Textarea(attrs={"rows": 3}))
    evidence_links = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
                                               label="Evidence included in this submission")

    def __init__(self, *args, action=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["evidence_links"].choices = [
            (link.pk, f"{link.evidence.original_name} ({link.evidence.sha256[:10]}…)")
            for link in action.evidence_links.select_related("evidence")
        ]


class NoteForm(forms.Form):
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class ComplianceForm(forms.Form):
    decision = forms.ChoiceField(choices=ComplianceDecisionValue.choices, widget=forms.RadioSelect)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, allow_exception=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not allow_exception:
            self.fields["decision"].choices = [
                c for c in ComplianceDecisionValue.choices if c[0] != ComplianceDecisionValue.EXCEPTION_GRANTED
            ]


class RiskForm(forms.Form):
    inherent = forms.ChoiceField(choices=RiskRating.choices, label="Inherent risk")
    residual = forms.ChoiceField(choices=RiskRating.choices, label="Residual risk")
    rationale = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))


class AcceptanceRequestForm(forms.Form):
    requirements = forms.ModelMultipleChoiceField(queryset=None, required=False, widget=forms.CheckboxSelectMultiple,
                                                  label="Unresolved requirements")
    technical_items = forms.ModelMultipleChoiceField(queryset=None, required=False,
                                                     widget=forms.CheckboxSelectMultiple,
                                                     label="Unresolved technical findings")
    business_owner = forms.ModelChoiceField(queryset=None)
    justification = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    compensating_controls = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    conditions = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), help_text="Write 'None' if there are none.")
    review_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    expiry_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, review=None, gaps=(), technical=(), **kwargs):
        from apps.reviews.models import ReviewRequirement, ReviewTechnicalItem

        super().__init__(*args, **kwargs)
        self.fields["requirements"].queryset = ReviewRequirement.objects.filter(pk__in=[g.pk for g in gaps])
        self.fields["requirements"].label_from_instance = lambda rr: f"{rr.control.code} — {rr.control.title}"
        self.fields["technical_items"].queryset = ReviewTechnicalItem.objects.filter(pk__in=[t.pk for t in technical])
        self.fields["technical_items"].label_from_instance = (
            lambda t: f"{t.control.source_id} — {t.control.control_name}"
        )
        reviewers = review.assignments.filter(role="REVIEWER").values_list("user_id", flat=True)
        self.fields["business_owner"].queryset = users_with_role(Role.BUSINESS_OWNER).exclude(pk__in=reviewers)

    def clean(self):
        data = super().clean()
        rd, ed = data.get("review_date"), data.get("expiry_date")
        if rd and ed and rd > ed:
            self.add_error("review_date", "The review date must be on or before the expiry date.")
        return data


class AcceptanceDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[(AcceptanceState.ACCEPTED, "Accept"), (AcceptanceState.DECLINED, "Decline")], widget=forms.RadioSelect
    )
    note = forms.CharField(label="Decision note", widget=forms.Textarea(attrs={"rows": 3}))


class CompletionForm(forms.Form):
    final_summary = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), label="Final review summary")
    conditions = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), initial="None",
                                 help_text="Write 'None' if there are no conditions.")
