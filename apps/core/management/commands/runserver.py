"""runserver that refuses non-loopback binding in the password-only stage (spec 4.7)."""
from django.conf import settings
from django.core.management.base import CommandError
from django.core.management.commands.runserver import Command as RunserverCommand

from apps.core.stage import is_password_only

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class Command(RunserverCommand):
    def inner_run(self, *args, **options):
        if is_password_only(settings):
            if self.addr not in LOOPBACK:
                raise CommandError("PASSWORD_ONLY_LOCAL may only bind to a loopback address.")
            from apps.content.models import RuleSet, TechnicalCatalogue

            if RuleSet.objects.filter(is_synthetic=False).exists() or \
                    TechnicalCatalogue.objects.filter(is_synthetic=False).exists():
                raise CommandError("Non-synthetic content is present; PASSWORD_ONLY_LOCAL is not permitted.")
        return super().inner_run(*args, **options)
