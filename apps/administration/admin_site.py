"""Restricted Django Admin (spec 3.4, 24.4).

A dedicated AdminSite with an explicit allow-list. No autodiscover, no Group /
Permission editor, no assessment-bearing models, no OTP devices, no AuditEvent.
Permissions come from the application Admin role (or the technical superuser),
not from arbitrary Django model permissions.
"""
from django import forms
from django.contrib import admin, messages
from django.contrib.auth.models import Group
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.models import Department, Role, SecuritySetting, User
from apps.accounts.services import generate_temporary_password, roles_conflict
from apps.assurance.models import EvidencePolicy
from apps.audit.services import record
from apps.content.models import RuleSet, TechnicalCatalogue


def is_admin_operator(user):
    return bool(
        user.is_active
        and getattr(user, "af_auth_complete", False)
        and (user.is_superuser or user.is_app_admin)
    )


class AssureFlowAdminSite(admin.AdminSite):
    site_header = "AssureFlow Administration"
    site_title = "AssureFlow Administration"
    index_title = "Accounts and configuration"
    enable_nav_sidebar = False
    final_catch_all_view = False

    def has_permission(self, request):
        return is_admin_operator(request.user)

    def login(self, request, extra_context=None):
        # Always use the full AssureFlow sign-in flow.
        return redirect(reverse("accounts:login"))

    def logout(self, request, extra_context=None):
        return redirect(reverse("accounts:login"))

    def get_urls(self):
        # Drop the password-change and generic "view on site" helpers.
        urls = super().get_urls()
        blocked = {"password_change", "password_change_done", "view_on_site"}
        return [u for u in urls if getattr(u, "name", None) not in blocked]


site = AssureFlowAdminSite(name="afadmin")


class RoleBasedAdmin(admin.ModelAdmin):
    """Permissions derive from the Admin role, not from Django model permissions."""

    allow_add = True
    allow_delete = False

    def has_module_permission(self, request):
        return is_admin_operator(request.user)

    def has_view_permission(self, request, obj=None):
        return is_admin_operator(request.user)

    def has_change_permission(self, request, obj=None):
        return is_admin_operator(request.user)

    def has_add_permission(self, request):
        return self.allow_add and is_admin_operator(request.user)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}


@admin.register(Department, site=site)
class DepartmentAdmin(RoleBasedAdmin):
    list_display = ("name", "is_active")
    search_fields = ("name",)


class UserAccountForm(forms.ModelForm):
    roles = forms.MultipleChoiceField(
        choices=[(r, r) for r in Role.ALL], widget=forms.CheckboxSelectMultiple, required=False,
        help_text="Admin cannot be combined with Requester, Reviewer or Business Owner.",
    )
    reset_password = forms.BooleanField(
        required=False, help_text="Issue a new temporary password. The user must change it after email verification."
    )

    class Meta:
        model = User
        fields = ("email", "employee_id", "full_name", "department", "is_active")

    def __init__(self, *args, **kwargs):
        self.operator = kwargs.pop("operator", None)
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["roles"].initial = list(self.instance.groups.values_list("name", flat=True))
        else:
            self.fields.pop("reset_password")
        if self.operator is not None and self.instance.pk == self.operator.pk:
            # An Admin cannot change their own roles or disable themselves.
            self.fields["roles"].disabled = True
            self.fields["is_active"].disabled = True

    def clean_roles(self):
        roles = self.cleaned_data.get("roles") or []
        if roles_conflict(roles):
            raise forms.ValidationError("Admin cannot be combined with a business-participant role on one account.")
        return roles

    def clean(self):
        data = super().clean()
        if not self.instance.is_superuser and not data.get("department") and set(data.get("roles") or []) & set(Role.BUSINESS):
            self.add_error("department", "Business users need a department.")
        return data


@admin.register(User, site=site)
class UserAdmin(RoleBasedAdmin):
    form = UserAccountForm
    list_display = ("full_name", "email", "employee_id", "department", "role_list", "is_active", "must_change_password")
    list_filter = ("is_active", "department")
    search_fields = ("full_name", "email", "employee_id")
    readonly_fields = ("must_change_password", "password_changed_at", "locked_until", "created_at")
    fieldsets = (
        (None, {"fields": ("email", "employee_id", "full_name", "department", "is_active")}),
        ("Application roles", {"fields": ("roles",)}),
        ("Credentials", {"fields": ("reset_password", "must_change_password", "password_changed_at", "locked_until")}),
    )

    @admin.display(description="Roles")
    def role_list(self, obj):
        return ", ".join(sorted(obj.groups.values_list("name", flat=True))) or "—"

    def get_queryset(self, request):
        # The technical superuser account is not managed through ordinary forms.
        return super().get_queryset(request).filter(is_superuser=False)

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        operator = request.user

        class BoundForm(form_class):
            def __init__(self, *args, **kw):
                kw["operator"] = operator
                super().__init__(*args, **kw)

        return BoundForm

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (
                (None, {"fields": ("email", "employee_id", "full_name", "department", "is_active")}),
                ("Application roles", {"fields": ("roles",)}),
            )
        return self.fieldsets

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_superuser:
            return False
        return super().has_change_permission(request, obj)

    def has_view_permission(self, request, obj=None):
        if obj is not None and obj.is_superuser:
            return False
        return super().has_view_permission(request, obj)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        before = {}
        if change:
            old = User.objects.get(pk=obj.pk)
            before = {"email": old.email, "employee_id": old.employee_id, "is_active": old.is_active,
                      "roles": sorted(old.groups.values_list("name", flat=True))}
        temp_password = None
        if not change or form.cleaned_data.get("reset_password"):
            temp_password = generate_temporary_password()
            obj.set_password(temp_password)
            obj.must_change_password = True
            obj.password_changed_at = None
        obj.is_superuser = False
        obj.is_staff = False
        super().save_model(request, obj, form, change)
        if "roles" in form.cleaned_data and not form.fields["roles"].disabled:
            roles = form.cleaned_data["roles"]
            obj.groups.set(Group.objects.filter(name__in=roles))
        obj.clear_role_cache()
        after = {"email": obj.email, "employee_id": obj.employee_id, "is_active": obj.is_active,
                 "roles": sorted(obj.groups.values_list("name", flat=True))}
        if before != after or temp_password:
            # Identity, status, role or credential change ends existing sessions.
            User.objects.filter(pk=obj.pk).update(session_version=obj.session_version + 1)
        changed = sorted(k for k in after if before.get(k) != after[k]) if change else ["created"]
        if temp_password and change:
            changed.append("password_reset")
        record(
            "admin.user_changed", actor=request.user, obj=obj,
            detail={"target_user": obj.employee_id, "changed": changed, "roles": after["roles"],
                    "is_active": obj.is_active},
            request=request,
        )
        if temp_password:
            messages.warning(
                request,
                f"Temporary password for {obj.employee_id}: {temp_password} — give it to the user through an "
                "approved channel. It is shown once and must be changed after sign-in.",
            )

    def save_related(self, request, form, formsets, change):
        # Groups are handled in save_model; do not let the admin save any M2M.
        pass


class SingletonAdmin(RoleBasedAdmin):
    def has_add_permission(self, request):
        return is_admin_operator(request.user) and not self.model.objects.exists()

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            obj.updated_by = request.user
            super().save_model(request, obj, form, change)
            record(
                f"admin.{self.model.__name__.lower()}_changed", actor=request.user, obj=obj,
                detail={"fields": sorted(form.changed_data)}, request=request,
            )


@admin.register(SecuritySetting, site=site)
class SecuritySettingAdmin(SingletonAdmin):
    list_display = ("__str__", "otp_lifetime_seconds", "idle_timeout_minutes", "max_session_minutes", "updated_at")
    exclude = ("updated_by",)


class EvidencePolicyForm(forms.ModelForm):
    allowed_types = forms.MultipleChoiceField(
        choices=EvidencePolicy.TYPE_CHOICES, widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = EvidencePolicy
        fields = ("allowed_types", "max_bytes")

    def clean_max_bytes(self):
        value = self.cleaned_data["max_bytes"]
        if value > 50 * 1024 * 1024:
            raise forms.ValidationError("Maximum size cannot exceed 50 MB.")
        return value


@admin.register(EvidencePolicy, site=site)
class EvidencePolicyAdmin(SingletonAdmin):
    form = EvidencePolicyForm
    list_display = ("__str__", "max_bytes", "updated_at")


class VersionMetadataAdmin(RoleBasedAdmin):
    """Read-only safe version metadata; no notes, rules, questions or mappings."""

    allow_add = False
    fields = ("version", "label", "source_sha256", "is_active", "is_synthetic", "loaded_at")
    readonly_fields = fields
    list_display = ("version", "label", "is_active", "is_synthetic", "loaded_at")

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(RuleSet, site=site)
class RuleSetAdmin(VersionMetadataAdmin):
    pass


@admin.register(TechnicalCatalogue, site=site)
class TechnicalCatalogueAdmin(VersionMetadataAdmin):
    pass


ALLOWED_ADMIN_MODELS = {Department, User, SecuritySetting, EvidencePolicy, RuleSet, TechnicalCatalogue}
