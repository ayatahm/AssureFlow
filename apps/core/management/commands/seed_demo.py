"""Load the synthetic demo content, users and a sample Draft review (idempotent).

Refuses to run outside the local-synthetic and test profiles: demo accounts have
a published password and must never exist on a shared or production system.
"""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Department, Role, SecuritySetting, User
from apps.assurance.models import EvidencePolicy
from apps.content.loader import load_catalogue, load_ruleset
from apps.content.models import ScopeTag
from apps.core.stage import PROFILE_LOCAL_SYNTHETIC, PROFILE_TEST
from apps.reviews.models import RequestType, Review
from apps.reviews.services.intake import create_review

SEED_DIR = Path(settings.BASE_DIR) / "seed_demo"
DEMO_PASSWORD = "Demo-Password-2026!"

DEPARTMENTS = ["Information Security", "Finance", "Retail Banking", "Operations", "IT Infrastructure"]
USERS = [
    ("sara.requester@demo.assureflow.local", "EMP1001", "Sara Demo-Requester", "Finance", [Role.REQUESTER]),
    ("omar.requester@demo.assureflow.local", "EMP1002", "Omar Demo-Requester", "Retail Banking", [Role.REQUESTER]),
    ("layla.reviewer@demo.assureflow.local", "EMP2001", "Layla Demo-Reviewer", "Information Security", [Role.REVIEWER]),
    ("yusuf.reviewer@demo.assureflow.local", "EMP2002", "Yusuf Demo-Reviewer", "Information Security", [Role.REVIEWER]),
    ("fatima.owner@demo.assureflow.local", "EMP3001", "Fatima Demo-Owner", "Finance", [Role.BUSINESS_OWNER]),
    ("admin@demo.assureflow.local", "EMP9001", "Alex Demo-Admin", "IT Infrastructure", [Role.ADMIN]),
]


class Command(BaseCommand):
    help = "Seed SYNTHETIC demo content and accounts (local-synthetic/test profiles only)."

    def add_arguments(self, parser):
        parser.add_argument("--content-only", action="store_true", help="Load only the synthetic rule set/catalogue.")

    @transaction.atomic
    def handle(self, content_only=False, **options):
        if settings.ASSUREFLOW_PROFILE not in (PROFILE_LOCAL_SYNTHETIC, PROFILE_TEST):
            raise CommandError("seed_demo runs only in the local-synthetic or test profile.")
        rs, rs_new = load_ruleset(SEED_DIR / "ruleset_demo.json")
        cat, cat_new = load_catalogue(SEED_DIR / "catalogue_demo.json")
        self.stdout.write(f"RuleSet {rs.version} ({'loaded' if rs_new else 'already present'}); "
                          f"Catalogue {cat.version} ({'loaded' if cat_new else 'already present'})")
        if not SecuritySetting.objects.exists():
            SecuritySetting.objects.create()
        if not EvidencePolicy.objects.exists():
            EvidencePolicy.objects.create(allowed_types=[t for t, _ in EvidencePolicy.TYPE_CHOICES])
        if content_only:
            return

        depts = {name: Department.objects.get_or_create(name=name)[0] for name in DEPARTMENTS}
        users = {}
        for email, emp, name, dept, roles in USERS:
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User.objects.create_user(email, emp, DEMO_PASSWORD, full_name=name, department=depts[dept],
                                                password_changed_at=timezone.now())
            user.groups.set(Group.objects.filter(name__in=roles))
            users[emp] = user
        self.stdout.write(f"{len(users)} demo accounts ready (password: {DEMO_PASSWORD})")

        if not Review.objects.filter(title="[Demo] Customer onboarding portal").exists():
            requester = users["EMP1001"]
            requester.af_auth_complete = True
            create_review(
                requester,
                {
                    "title": "[Demo] Customer onboarding portal",
                    "purpose": "Synthetic example: a web portal for new retail customers to open accounts.",
                    "goals": "Collect applicant details, verify identity documents and create customer records.",
                    "department": depts["Finance"],
                    "project_owner": "Sara Demo-Requester",
                    "request_type": RequestType.NEW_SYSTEM,
                    "target_date": timezone.localdate() + timedelta(days=60),
                },
                reviewer=users["EMP2001"],
                scope_tags=ScopeTag.objects.filter(code__in=["APPLICATION", "API", "PERSONAL_DATA"]),
            )
            self.stdout.write("Created a sample Draft review.")
