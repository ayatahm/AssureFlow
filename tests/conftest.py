import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings as dj_settings
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.accounts.models import Department, Role, SecuritySetting, User
from apps.assurance.models import EvidencePolicy
from apps.content.loader import load_catalogue, load_ruleset
from apps.content.models import ScopeTag
from apps.reviews.models import RequestType

SEED = Path(dj_settings.BASE_DIR) / "seed_demo"
PASSWORD = "Correct-Horse-Battery-9"

# Answers that keep most controls and produce the CONFIDENTIAL classification.
TRIAGE_BROAD = {
    "impact": "MODERATE", "value": "MODERATE", "T1": "YES", "T2": "YES", "T3": "YES", "T4": "NO",
    "T5": "YES", "T6": "YES", "T7": "NO", "T8": "NO",
}
# Minimal answers: PUBLIC, most gates skip.
TRIAGE_MINIMAL = {
    "impact": "LOW", "value": "LOW", "T1": "NO", "T2": "NO", "T3": "NO", "T4": "NO",
    "T5": "NO", "T6": "NO", "T7": "NO", "T8": "NO",
}


@pytest.fixture(autouse=True)
def _isolated(settings, tmp_path):
    settings.EVIDENCE_ROOT = tmp_path / "evidence"
    settings.AUTH_FLOW_STAGE = "EMAIL_CODE_REQUIRED"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.PASSWORD_HASHERS = ["tests.hashers.FastArgon2PasswordHasher"]
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]


@pytest.fixture
def content(db):
    rs, _ = load_ruleset(SEED / "ruleset_demo.json")
    cat, _ = load_catalogue(SEED / "catalogue_demo.json")
    SecuritySetting.objects.get_or_create()
    if not EvidencePolicy.objects.exists():
        EvidencePolicy.objects.create(allowed_types=["application/pdf", "image/png", "image/jpeg"])
    return rs, cat


@pytest.fixture
def dept(db):
    return Department.objects.get_or_create(name="Finance")[0]


_counter = {"n": 0}


@pytest.fixture
def make_user(db, dept):
    def factory(*roles, name=None, verified=True, **extra):
        _counter["n"] += 1
        n = _counter["n"]
        user = User.objects.create_user(
            f"user{n}@example.test", f"E{n:05d}", PASSWORD, full_name=name or f"User {n}", department=dept,
            password_changed_at=timezone.now(), **extra,
        )
        # get_or_create: transactional tests flush the migration-created groups.
        user.groups.set([Group.objects.get_or_create(name=r)[0] for r in roles])
        user.clear_role_cache()
        if verified:
            user.af_auth_complete = True  # what AuthStageMiddleware sets after the full sign-in
        return user
    return factory


@pytest.fixture
def people(make_user):
    return {
        "requester": make_user(Role.REQUESTER, name="Rita Requester"),
        "requester2": make_user(Role.REQUESTER, name="Rami Requester"),
        "reviewer": make_user(Role.REVIEWER, name="Rhea Reviewer"),
        "reviewer2": make_user(Role.REVIEWER, name="Ravi Reviewer"),
        "owner": make_user(Role.BUSINESS_OWNER, name="Bea Owner"),
        "admin": make_user(Role.ADMIN, name="Ada Admin"),
        "outsider": make_user(Role.REQUESTER, Role.REVIEWER, name="Otto Outsider"),
    }


def intake_data(dept, **overrides):
    data = {
        "title": "Test system", "purpose": "Purpose", "goals": "Goals", "department": dept,
        "project_owner": "Owner", "request_type": RequestType.NEW_SYSTEM,
        "target_date": timezone.localdate() + timedelta(days=30),
    }
    data.update(overrides)
    return data


@pytest.fixture
def draft_review(content, people, dept):
    from apps.reviews.services.intake import create_review

    return create_review(
        people["requester"], intake_data(dept), reviewer=people["reviewer"],
        scope_tags=ScopeTag.objects.filter(code__in=["APPLICATION", "API"]),
    )


def triage_and_generate(review, actor, answers=None):
    from apps.reviews.services.generation import generate_assessment
    from apps.reviews.services.triage import save_triage

    save_triage(actor, review, dict(answers or TRIAGE_BROAD))
    review, _ = generate_assessment(actor, review)
    return review


def answer_all(review, actor, answer="YES"):
    from apps.reviews.services.assessment import save_answer

    for rq in review.questions.all():
        save_answer(actor, review, rq, answer)


@pytest.fixture
def assessment_review(draft_review, people):
    return triage_and_generate(draft_review, people["requester"])


@pytest.fixture
def in_review(assessment_review, people):
    from apps.reviews.services.assessment import submit_assessment

    answer_all(assessment_review, people["requester"])
    return submit_assessment(people["requester"], assessment_review)


def refresh(review):
    review.refresh_from_db()
    return review


def full_login(client, user, password=PASSWORD, mailbox=None):
    """Drive the real sign-in flow: password -> emailed code -> session."""
    from django.core import mail

    resp = client.post("/account/login/", {"identifier": user.email, "password": password})
    assert resp.status_code == 302, resp.content[:500]
    if resp["Location"].endswith("/account/verify/"):
        code = extract_code(mail.outbox[-1].body)
        resp = client.post("/account/verify/", {"code": code})
    return resp


def extract_code(body):
    return re.search(r"\b(\d{6})\b", body).group(1)
