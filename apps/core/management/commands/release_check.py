"""Release gate (spec 30.6): full verification stage, no skipped mandatory tests."""
import os
import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.stage import EMAIL_CODE_REQUIRED


class Command(BaseCommand):
    help = "Run the full test suite under EMAIL_CODE_REQUIRED and fail on any failure or skipped test."

    def handle(self, **options):
        if settings.AUTH_FLOW_STAGE != EMAIL_CODE_REQUIRED:
            raise CommandError("Release gate: AUTH_FLOW_STAGE must be EMAIL_CODE_REQUIRED.")
        env = dict(os.environ, AUTH_FLOW_STAGE=EMAIL_CODE_REQUIRED, ASSUREFLOW_PROFILE="test", DJANGO_DEBUG="False",
                   DJANGO_SETTINGS_MODULE="config.test_settings")
        report = os.path.join(settings.BASE_DIR, "private_data", "release_junit.xml")
        os.makedirs(os.path.dirname(report), exist_ok=True)
        cmd = [sys.executable, "-m", "pytest", "-q", "-rs", f"--junitxml={report}"]
        self.stdout.write("Running: " + " ".join(cmd))
        result = subprocess.run(cmd, env=env, cwd=settings.BASE_DIR)
        if result.returncode != 0:
            raise CommandError("Release gate failed: test failures.")
        with open(report, encoding="utf-8") as fh:
            xml = fh.read()
        import re

        skipped = sum(int(n) for n in re.findall(r'skipped="(\d+)"', xml))
        if skipped:
            raise CommandError(f"Release gate failed: {skipped} skipped test(s). Mandatory checks must run.")
        self.stdout.write(self.style.SUCCESS(f"Release gate passed. JUnit report: {report}"))
