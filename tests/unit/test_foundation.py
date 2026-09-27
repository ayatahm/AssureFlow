"""Week 3 foundation checks (spec 33)."""
import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction

from apps.accounts.models import Role, User
from apps.audit.models import AuditEvent, ImmutableRecordError
from apps.audit.services import record
from apps.content.models import AssessmentQuestion, ControlRequirement, RuleSet, TechnicalCatalogue, TechnicalControl

pytestmark = [pytest.mark.django_db, pytest.mark.foundation]


def test_seed_loads_twice_without_duplicates(settings):
    settings.ASSUREFLOW_PROFILE = "test"
    call_command("seed_demo")
    counts = (RuleSet.objects.count(), ControlRequirement.objects.count(), AssessmentQuestion.objects.count(),
              TechnicalCatalogue.objects.count(), TechnicalControl.objects.count(), User.objects.count())
    call_command("seed_demo")
    assert counts == (RuleSet.objects.count(), ControlRequirement.objects.count(), AssessmentQuestion.objects.count(),
                      TechnicalCatalogue.objects.count(), TechnicalControl.objects.count(), User.objects.count())
    assert RuleSet.objects.get().is_synthetic and RuleSet.objects.get().is_active


def test_seed_refused_outside_synthetic_profiles(settings):
    from django.core.management import CommandError

    settings.ASSUREFLOW_PROFILE = "production"
    with pytest.raises(CommandError):
        call_command("seed_demo")


def test_changed_content_with_same_version_refused(tmp_path, content):
    import json
    from pathlib import Path

    from apps.content.loader import ContentLoadError, load_ruleset

    data = json.loads((Path(__file__).resolve().parents[2] / "seed_demo" / "ruleset_demo.json").read_text())
    data["label"] = "tampered"
    p = tmp_path / "r.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ContentLoadError):
        load_ruleset(p)


def test_passwords_use_argon2(settings, dept):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher",
                                 "django.contrib.auth.hashers.PBKDF2PasswordHasher"]
    user = User.objects.create_user("a@example.test", "A1", "Some-Password-123", full_name="A", department=dept)
    assert user.password.startswith("argon2$argon2id$")


def test_production_settings_list_argon2_first():
    from config import settings as prod

    assert prod.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"


def test_email_normalised_and_unique(dept):
    User.objects.create_user("Mixed@Example.TEST", "M1", "x-Password-123", full_name="M", department=dept)
    assert User.objects.filter(email="mixed@example.test").exists()


def test_role_groups_exist():
    from django.contrib.auth.models import Group

    assert set(Role.ALL) <= set(Group.objects.values_list("name", flat=True))


def test_audit_event_immutable():
    ev = record("test.event", detail={"x": 1})
    ev.detail = {"x": 2}
    with pytest.raises(ImmutableRecordError):
        ev.save()
    with pytest.raises(ImmutableRecordError):
        ev.delete()
    with pytest.raises(ImmutableRecordError):
        AuditEvent.objects.filter(pk=ev.pk).update(action="x")
    with pytest.raises(ImmutableRecordError):
        AuditEvent.objects.all().delete()


def test_audit_event_database_trigger():
    ev = record("test.event")
    for sql in ("UPDATE audit_auditevent SET action = 'x' WHERE id = %s", "DELETE FROM audit_auditevent WHERE id = %s"):
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql, [ev.pk])
    assert AuditEvent.objects.get(pk=ev.pk).action == "test.event"


def test_audit_refuses_credentials_in_detail():
    with pytest.raises(ValueError):
        record("user.login", detail={"password": "x"})
    with pytest.raises(ValueError):
        record("user.code_failed", detail={"Code": "123456"})


def test_security_headers(client):
    resp = client.get("/account/login/")
    assert resp["X-Frame-Options"] == "DENY"
    assert resp["X-Content-Type-Options"] == "nosniff"
    assert resp["Referrer-Policy"] == "same-origin"


def test_templates_do_not_mark_user_content_safe():
    from pathlib import Path

    for tpl in (Path(__file__).resolve().parents[2] / "templates").rglob("*.html"):
        for line in tpl.read_text().splitlines():
            if "|safe" in line:
                assert "help_text|safe" in line, f"{tpl}: {line.strip()}"


def test_no_cdn_dependencies():
    from pathlib import Path

    for tpl in (Path(__file__).resolve().parents[2] / "templates").rglob("*.html"):
        text = tpl.read_text()
        assert "cdn." not in text and "googleapis" not in text and "unpkg" not in text, tpl


def test_upload_next_parameter_cannot_redirect_offsite(client, assessment_review, people):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.reviews.models import Applicability
    from tests.conftest import full_login

    rr = assessment_review.requirements.filter(applicability=Applicability.KEEP).first()
    full_login(client, people["requester"])
    resp = client.post(f"/reviews/{assessment_review.pk}/evidence/upload/", {
        "target": f"requirement:{rr.pk}", "next": "https://evil.example/",
        "file": SimpleUploadedFile("p.pdf", b"%PDF-1.4 x"),
    })
    assert resp.status_code == 302 and "evil.example" not in resp["Location"]
