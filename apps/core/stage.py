"""Authentication-stage and deployment-profile guard (spec section 4.7).

This module is imported by ``config/settings.py`` so it must not import Django
models. ``validate_runtime_configuration`` raises ``ImproperlyConfigured`` for
any unsafe combination, which stops the process before it serves a request.
"""
from django.core.exceptions import ImproperlyConfigured

EMAIL_CODE_REQUIRED = "EMAIL_CODE_REQUIRED"
PASSWORD_ONLY_LOCAL = "PASSWORD_ONLY_LOCAL"
AUTH_STAGES = {EMAIL_CODE_REQUIRED, PASSWORD_ONLY_LOCAL}

PROFILE_LOCAL_SYNTHETIC = "local-synthetic"
PROFILE_TEST = "test"
PROFILE_SHARED = "shared"
PROFILE_PRODUCTION = "production"
PROFILES = {PROFILE_LOCAL_SYNTHETIC, PROFILE_TEST, PROFILE_SHARED, PROFILE_PRODUCTION}

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}

INSECURE_DEV_KEY_PREFIX = "insecure-local-dev-only"

PASSWORD_ONLY_BANNER = "LOCAL SYNTHETIC DEMO — EMAIL VERIFICATION NOT YET IMPLEMENTED"


def validate_runtime_configuration(*, stage, profile, debug, allowed_hosts, release_build, secret_key):
    """Fail closed on unsafe configuration.

    Rules:
    * the stage and profile must be known values;
    * password-only access requires DEBUG, the local synthetic profile,
      loopback-only hosts and a non-release build;
    * shared/production profiles and release builds require DEBUG=False and a
      real secret key.
    """
    if stage not in AUTH_STAGES:
        raise ImproperlyConfigured(f"Unknown AUTH_FLOW_STAGE {stage!r}.")
    if profile not in PROFILES:
        raise ImproperlyConfigured(f"Unknown ASSUREFLOW_PROFILE {profile!r}.")

    if stage == PASSWORD_ONLY_LOCAL:
        if release_build:
            raise ImproperlyConfigured("A release build cannot use PASSWORD_ONLY_LOCAL.")
        if profile != PROFILE_LOCAL_SYNTHETIC:
            raise ImproperlyConfigured(
                "PASSWORD_ONLY_LOCAL is permitted only with ASSUREFLOW_PROFILE=local-synthetic."
            )
        if not debug:
            raise ImproperlyConfigured("PASSWORD_ONLY_LOCAL requires DEBUG=True (local development only).")
        hosts = set(allowed_hosts or [])
        if not hosts or not hosts <= LOOPBACK_HOSTS:
            raise ImproperlyConfigured("PASSWORD_ONLY_LOCAL requires ALLOWED_HOSTS to be loopback only.")

    if profile in (PROFILE_SHARED, PROFILE_PRODUCTION) or release_build:
        if debug:
            raise ImproperlyConfigured("DEBUG must be False for shared/production profiles and release builds.")
        if not secret_key or secret_key.startswith(INSECURE_DEV_KEY_PREFIX) or len(secret_key) < 40:
            raise ImproperlyConfigured("A strong DJANGO_SECRET_KEY is required outside local development.")


def is_password_only(settings_obj):
    return getattr(settings_obj, "AUTH_FLOW_STAGE", EMAIL_CODE_REQUIRED) == PASSWORD_ONLY_LOCAL


def assert_private_content_allowed(settings_obj):
    """Approved private-content import refuses to run in the password-only stage."""
    if is_password_only(settings_obj):
        raise ImproperlyConfigured(
            "Private assessment content cannot be loaded while AUTH_FLOW_STAGE=PASSWORD_ONLY_LOCAL."
        )
    if getattr(settings_obj, "ASSUREFLOW_PROFILE", None) == PROFILE_LOCAL_SYNTHETIC:
        raise ImproperlyConfigured(
            "Private assessment content cannot be loaded into the local-synthetic profile."
        )
