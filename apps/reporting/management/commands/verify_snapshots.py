from django.core.management.base import BaseCommand, CommandError

from apps.reporting.models import ReportSnapshot
from apps.reporting.services import verify_snapshot


class Command(BaseCommand):
    help = "Re-serialize each stored report snapshot (af-json-v1) and compare with its recorded SHA-256."

    def handle(self, **options):
        bad = 0
        total = 0
        for snap in ReportSnapshot.objects.order_by("pk"):
            total += 1
            if not verify_snapshot(snap):
                bad += 1
                self.stdout.write(self.style.ERROR(f"MISMATCH: review {snap.review_id} revision {snap.revision}"))
        self.stdout.write(f"Checked {total} snapshot(s); {bad} mismatch(es).")
        if bad:
            raise CommandError("Snapshot verification failed.")
