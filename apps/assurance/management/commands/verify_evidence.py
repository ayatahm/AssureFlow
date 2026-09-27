from django.core.management.base import BaseCommand, CommandError

from apps.assurance.services.evidence import verify_all


class Command(BaseCommand):
    help = "Recalculate SHA-256 for every stored evidence file and report missing or modified files."

    def handle(self, **options):
        counts = {"OK": 0, "MISSING": 0, "MODIFIED": 0}
        for ev, status in verify_all():
            counts[status] += 1
            if status != "OK":
                self.stdout.write(self.style.ERROR(f"{status}: evidence {ev.pk} ({ev.stored_name}) review {ev.review_id}"))
        self.stdout.write(f"Checked {sum(counts.values())} file(s): {counts['OK']} OK, "
                          f"{counts['MISSING']} missing, {counts['MODIFIED']} modified.")
        if counts["MISSING"] or counts["MODIFIED"]:
            raise CommandError("Evidence integrity check failed.")
