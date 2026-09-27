from django.core.management.base import BaseCommand, CommandError

from apps.content.loader import ContentLoadError, load_catalogue


class Command(BaseCommand):
    help = "Load a versioned Technical Catalogue JSON file."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--no-activate", action="store_true")

    def handle(self, path, no_activate=False, **options):
        try:
            cat, created = load_catalogue(path, activate=not no_activate)
        except (ContentLoadError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        verb = "Loaded" if created else "Already loaded"
        self.stdout.write(self.style.SUCCESS(f"{verb} Technical Catalogue {cat.version}"))
