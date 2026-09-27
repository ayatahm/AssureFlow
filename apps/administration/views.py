from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from apps.accounts.models import Role
from apps.core.errors import ServiceError
from apps.reviews.models import ParticipantRole, Review

from . import services

User = get_user_model()


class AssignmentForm(forms.Form):
    review = forms.ModelChoiceField(queryset=Review.objects.none(), to_field_name="pk")
    user = forms.ModelChoiceField(queryset=User.objects.none())
    role = forms.ChoiceField(choices=ParticipantRole.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Labels show reference only — never assessment content.
        self.fields["review"].queryset = Review.objects.only("pk", "reference").order_by("-created_at")
        self.fields["review"].label_from_instance = lambda r: r.reference
        self.fields["user"].queryset = (
            User.objects.filter(is_active=True, is_superuser=False)
            .exclude(groups__name=Role.ADMIN).order_by("full_name")
        )


def _require_admin(request):
    if not services.is_admin_operator(request.user):
        raise PermissionDenied("administration")


def review_assignments(request):
    _require_admin(request)
    form = AssignmentForm(request.POST or None)
    if request.method == "POST":
        try:
            if "remove" in request.POST:
                services.unassign(request.user, int(request.POST["remove"]), request=request)
                messages.success(request, "Assignment removed.")
                return redirect("administration:assignments")
            if form.is_valid():
                services.assign(request.user, form.cleaned_data["review"].pk, form.cleaned_data["user"],
                                form.cleaned_data["role"], request=request)
                messages.success(request, "Participant assigned.")
                return redirect("administration:assignments")
        except ServiceError as exc:
            messages.error(request, exc.message)
        except (ValueError, KeyError):
            messages.error(request, "Invalid request.")
    return render(request, "administration/assignments.html", {"rows": services.review_metadata(), "form": form})


def account_events(request):
    _require_admin(request)
    return render(request, "administration/account_events.html", {"events": services.account_events()})
