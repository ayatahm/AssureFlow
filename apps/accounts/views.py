from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import services as auth
from .forms import CodeForm, LoginForm, NewPasswordForm


def login_view(request):
    if request.user.is_authenticated and request.session.get(auth.AUTH_COMPLETE):
        return redirect("core:dashboard")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ok, message = auth.begin_login(request, form.cleaned_data["identifier"], form.cleaned_data["password"])
        if ok:
            return _next_step(request)
        form.add_error(None, message)
    return render(request, "accounts/login.html", {"form": form})


def _next_step(request):
    user = auth.pending_user(request)
    if user is None:
        return redirect("accounts:login")
    if not auth.code_step_satisfied(request):
        return redirect("accounts:verify")
    if auth.needs_password_change(user):
        return redirect("accounts:password_change")
    auth.complete_login(request, user)
    return redirect("core:dashboard")


def verify_view(request):
    user = auth.pending_user(request)
    if user is None:
        return redirect("accounts:login")
    if auth.code_step_satisfied(request):
        return _next_step(request)
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ok, message = auth.verify_code(request, user, form.cleaned_data["code"])
        if ok:
            return _next_step(request)
        if auth.pending_user(request) is None:
            messages.error(request, message)
            return redirect("accounts:login")
        form.add_error("code", message)
    policy = auth.SecuritySetting.current()
    return render(
        request,
        "accounts/verify.html",
        {"form": form, "masked_email": auth.mask_email(user.email), "minutes": max(1, policy.otp_lifetime_seconds // 60)},
    )


@require_POST
def resend_view(request):
    user = auth.pending_user(request)
    if user is None or auth.code_step_satisfied(request):
        return redirect("accounts:login")
    ok, message = auth.send_code(request, user)
    (messages.success if ok else messages.warning)(request, message)
    return redirect("accounts:verify")


def password_change_view(request):
    """Reachable for a pending login that has passed the code step, or a signed-in user."""
    if request.user.is_authenticated and request.session.get(auth.AUTH_COMPLETE):
        user, pending = request.user, False
    else:
        user = auth.pending_user(request)
        if user is None:
            return redirect("accounts:login")
        if not auth.code_step_satisfied(request):
            return redirect("accounts:verify")
        pending = True
    form = NewPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        auth.change_password(request, user, form.cleaned_data["new_password1"])
        if pending:
            auth.complete_login(request, user)
        else:
            # Password hash changed: keep this session, end others.
            from django.contrib.auth import update_session_auth_hash

            update_session_auth_hash(request, user)
        messages.success(request, "Your password has been changed.")
        return redirect("core:dashboard")
    return render(request, "accounts/password_change.html", {"form": form, "forced": auth.needs_password_change(user)})


@require_POST
def logout_view(request):
    auth.clear_pending(request)
    auth.logout(request)
    return redirect("accounts:login")
