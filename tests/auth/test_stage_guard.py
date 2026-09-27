"""Development-stage and profile guard (spec 4.7). Foundation + release checks."""
import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.core.stage import validate_runtime_configuration

SAFE_KEY = "x" * 50

pytestmark = pytest.mark.release


def cfg(**kw):
    base = dict(stage="PASSWORD_ONLY_LOCAL", profile="local-synthetic", debug=True, allowed_hosts=["127.0.0.1"],
                release_build=False, secret_key="insecure-local-dev-only-key")
    base.update(kw)
    return base


def test_local_synthetic_password_only_is_allowed():
    validate_runtime_configuration(**cfg())


@pytest.mark.parametrize("override", [
    {"profile": "production", "debug": False, "secret_key": SAFE_KEY},
    {"profile": "shared", "debug": False, "secret_key": SAFE_KEY},
    {"profile": "test"},
    {"debug": False},
    {"allowed_hosts": ["assureflow.bank.local"]},
    {"allowed_hosts": ["127.0.0.1", "0.0.0.0"]},
    {"allowed_hosts": []},
    {"release_build": True},
])
def test_unsafe_password_only_configurations_fail_closed(override):
    with pytest.raises(ImproperlyConfigured):
        validate_runtime_configuration(**cfg(**override))


def test_unknown_stage_rejected():
    with pytest.raises(ImproperlyConfigured):
        validate_runtime_configuration(**cfg(stage="NO_AUTH"))


def test_production_requires_debug_off_and_real_key():
    with pytest.raises(ImproperlyConfigured):
        validate_runtime_configuration(**cfg(stage="EMAIL_CODE_REQUIRED", profile="production", debug=True,
                                             secret_key=SAFE_KEY))
    with pytest.raises(ImproperlyConfigured):
        validate_runtime_configuration(**cfg(stage="EMAIL_CODE_REQUIRED", profile="production", debug=False))
    validate_runtime_configuration(**cfg(stage="EMAIL_CODE_REQUIRED", profile="production", debug=False,
                                         secret_key=SAFE_KEY, allowed_hosts=["assureflow.bank.local"]))


def test_settings_module_refuses_unsafe_environment():
    """Importing settings with an unsafe environment raises at startup."""
    import os
    import subprocess
    import sys

    env = dict(os.environ, AUTH_FLOW_STAGE="PASSWORD_ONLY_LOCAL", ASSUREFLOW_PROFILE="production",
               DJANGO_DEBUG="False", DJANGO_SECRET_KEY=SAFE_KEY, DJANGO_SETTINGS_MODULE="config.settings")
    result = subprocess.run([sys.executable, "-c", "import django; django.setup()"], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0 and "ImproperlyConfigured" in result.stderr


def test_release_test_run_uses_full_verification(settings):
    from django.conf import settings as live

    assert live.AUTH_FLOW_STAGE == "EMAIL_CODE_REQUIRED"


@pytest.mark.django_db
def test_private_content_import_refused_in_password_only(settings, tmp_path):
    import json
    from pathlib import Path

    from apps.content.loader import load_ruleset

    settings.AUTH_FLOW_STAGE = "PASSWORD_ONLY_LOCAL"
    data = json.loads((Path(settings.BASE_DIR) / "seed_demo" / "ruleset_demo.json").read_text())
    data["synthetic"] = False
    data["version"] = "PRIVATE-1"
    path = tmp_path / "private.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ImproperlyConfigured):
        load_ruleset(path)


@pytest.mark.django_db
def test_password_only_stage_shows_banner_and_skips_code(client, make_user, content, settings):
    from apps.accounts.models import Role
    from tests.conftest import PASSWORD

    settings.AUTH_FLOW_STAGE = "PASSWORD_ONLY_LOCAL"
    user = make_user(Role.REQUESTER, verified=False)
    resp = client.post("/account/login/", {"identifier": user.email, "password": PASSWORD})
    assert resp["Location"] == "/dashboard/"
    body = client.get("/dashboard/").content.decode()
    assert "LOCAL SYNTHETIC DEMO — EMAIL VERIFICATION NOT YET IMPLEMENTED" in body
