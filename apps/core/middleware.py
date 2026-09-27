"""Enforces the full sign-in stage and session limits on every protected route."""
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services as auth
from apps.accounts.models import SecuritySetting
from apps.core.stage import is_password_only

# Routes reachable without a completed sign-in (the limited pending-login routes).
PUBLIC_PREFIXES = ("/static/",)
PENDING_ROUTES = ("accounts:login", "accounts:verify", "accounts:resend", "accounts:password_change", "accounts:logout")


def _parse(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class AuthStageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        exempt = path.startswith(PUBLIC_PREFIXES) or path in {reverse(n) for n in PENDING_ROUTES}
        user = request.user

        if user.is_authenticated:
            problem = self._session_problem(request, user)
            if problem:
                auth.logout(request, reason=problem)
                if problem in ("idle_timeout", "absolute_timeout"):
                    messages.info(request, "Your session has ended. Please sign in again.")
                return redirect("accounts:login")
            user.af_auth_complete = True
            if auth.needs_password_change(user) and not exempt:
                return redirect("accounts:password_change")
        elif not exempt:
            return redirect(f"{reverse('accounts:login')}")

        return self.get_response(request)

    def _session_problem(self, request, user):
        session = request.session
        if not session.get(auth.AUTH_COMPLETE):
            return "incomplete_sign_in"
        if not is_password_only(settings):
            is_verified = getattr(user, "is_verified", None)
            if not (callable(is_verified) and is_verified()):
                return "email_code_not_verified"
        policy = SecuritySetting.current()
        now = timezone.now()
        started = _parse(session.get(auth.SESSION_STARTED))
        last = _parse(session.get(auth.LAST_ACTIVITY))
        if started is None or now - started > timedelta(minutes=policy.max_session_minutes):
            return "absolute_timeout"
        if last is None or now - last > timedelta(minutes=policy.idle_timeout_minutes):
            return "idle_timeout"
        session[auth.LAST_ACTIVITY] = now.isoformat()
        session.set_expiry(policy.idle_timeout_minutes * 60)
        return None
