from django.core.management.base import BaseCommand, CommandError

from apps.content.loader import ContentLoadError, load_ruleset


class Command(BaseCommand):
    help = "Load a versioned RuleSet JSON file (synthetic demo or approved private content)."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--baseline", help="Frozen conformance baseline file whose hash is recorded.")
        parser.add_argument("--no-activate", action="store_true")

    def handle(self, path, baseline=None, no_activate=False, **options):
        try:
            rs, created = load_ruleset(path, activate=not no_activate, baseline_path=baseline)
        except (ContentLoadError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        verb = "Loaded" if created else "Already loaded"
        self.stdout.write(self.style.SUCCESS(f"{verb} RuleSet {rs.version} (sha256 {rs.source_sha256[:12]}…)"))
