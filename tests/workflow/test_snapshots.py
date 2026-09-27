"""Deterministic snapshot serialization and immutability (spec 18.2, 30.4)."""
import json
import threading
from datetime import datetime, timezone as dt_timezone

import pytest
from django.db import connection

from apps.audit.models import ImmutableRecordError
from apps.reporting.models import ReportSnapshot
from apps.reporting.serialization import SnapshotTypeError, iso_utc, snapshot_bytes, snapshot_sha256
from apps.reporting.services import verify_snapshot
from tests.conftest import refresh

from .helpers import complete, ready_to_complete, record_risk


def test_insertion_order_does_not_change_bytes():
    a = {"b": 1, "a": {"y": [1, 2], "x": "é"}}
    b = {"a": {"x": "é", "y": [1, 2]}, "b": 1}
    assert snapshot_bytes(a) == snapshot_bytes(b)


def test_harmless_whitespace_in_stored_json_is_irrelevant():
    data = {"k": [1, "two"], "n": None}
    reparsed = json.loads('{ "n" : null ,\n  "k": [ 1 , "two" ] }')
    assert snapshot_sha256(data) == snapshot_sha256(reparsed)


def test_arrays_are_not_reordered():
    assert snapshot_bytes({"x": [2, 1]}) != snapshot_bytes({"x": [1, 2]})


def test_unicode_survives_as_utf8():
    raw = snapshot_bytes({"t": "مرحبا ✓"})
    assert "مرحبا ✓".encode("utf-8") in raw


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1.5, datetime.now(dt_timezone.utc), {1, 2}, (1, 2)])
def test_unsupported_values_rejected(bad):
    with pytest.raises(SnapshotTypeError):
        snapshot_bytes({"v": bad})


def test_non_string_keys_rejected():
    with pytest.raises(SnapshotTypeError):
        snapshot_bytes({1: "x"})


def test_documented_timestamp_format():
    ts = datetime(2026, 9, 27, 10, 5, 3, 120, tzinfo=dt_timezone.utc)
    assert iso_utc(ts) == "2026-09-27T10:05:03.000120Z"
    with pytest.raises(SnapshotTypeError):
        iso_utc(datetime(2026, 1, 1))


@pytest.mark.django_db
def test_issued_snapshot_verifies_and_detects_change(in_review, people):
    ready_to_complete(in_review, people["reviewer"])
    snap = complete(refresh(in_review), people["reviewer"])
    stored = ReportSnapshot.objects.get(pk=snap.pk)
    assert stored.serializer_version == "af-json-v1"
    assert verify_snapshot(stored)
    assert "sha256" not in stored.data  # hash excluded from hashed payload
    stored.data["outcome"]["summary"] = "tampered"
    assert not verify_snapshot(stored)


@pytest.mark.django_db
def test_snapshot_collections_sorted(in_review, people):
    ready_to_complete(in_review, people["reviewer"])
    snap = complete(refresh(in_review), people["reviewer"])
    codes = [r["code"] for r in snap.data["requirements"]]
    assert codes == list(in_review.requirements.order_by("order").values_list("control__code", flat=True))
    items = [i["source_id"] for i in snap.data["technical"]["items"]]
    assert items == sorted(items)


@pytest.mark.django_db
def test_snapshot_immutable_and_report_views_do_not_mutate(client, in_review, people):
    from tests.conftest import full_login

    ready_to_complete(in_review, people["reviewer"])
    snap = complete(refresh(in_review), people["reviewer"])
    with pytest.raises(ImmutableRecordError):
        snap.save()
    with pytest.raises(ImmutableRecordError):
        ReportSnapshot.objects.filter(pk=snap.pk).update(revision=2)
    with pytest.raises(ImmutableRecordError):
        snap.delete()
    full_login(client, people["requester"])
    for _ in range(2):
        assert client.get(f"/reviews/{in_review.pk}/report/").status_code == 200
    assert ReportSnapshot.objects.filter(review=in_review).count() == 1
    assert ReportSnapshot.objects.get(pk=snap.pk).sha256 == snap.sha256


@pytest.mark.django_db
def test_database_trigger_blocks_raw_update(in_review, people):
    ready_to_complete(in_review, people["reviewer"])
    snap = complete(refresh(in_review), people["reviewer"])
    from django.db import DatabaseError, transaction

    with pytest.raises(DatabaseError):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("UPDATE reporting_reportsnapshot SET sha256 = %s WHERE id = %s", ["0" * 64, snap.pk])


@pytest.mark.django_db
def test_later_risk_record_does_not_change_issued_report(in_review, people):
    reviewer = people["reviewer"]
    ready_to_complete(in_review, reviewer)
    snap = complete(refresh(in_review), reviewer)
    risk_id = snap.data["risk"]["id"]
    r = refresh(in_review)
    assert r.final_risk_record_id == risk_id
    from apps.core.permissions import PermissionDenied

    with pytest.raises(PermissionDenied):
        record_risk(r, reviewer, residual="CRITICAL")
    assert ReportSnapshot.objects.get(pk=snap.pk).data["risk"]["id"] == risk_id


@pytest.mark.django_db(transaction=True)
def test_simultaneous_completion_issues_one_snapshot(content, people, dept):
    from apps.reviews.services.assessment import submit_assessment
    from apps.reviews.services.intake import create_review
    from tests.conftest import answer_all, intake_data, triage_and_generate

    review = create_review(people["requester"], intake_data(dept), reviewer=people["reviewer"])
    review = triage_and_generate(review, people["requester"])
    answer_all(review, people["requester"])
    submit_assessment(people["requester"], review)
    ready_to_complete(refresh(review), people["reviewer"])

    results = []
    barrier = threading.Barrier(2)

    def worker():
        from django.db import connections

        try:
            barrier.wait()
            complete(refresh(type(review).objects.get(pk=review.pk)), people["reviewer"])
            results.append("ok")
        except Exception as exc:  # noqa: BLE001
            results.append(type(exc).__name__)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1
    assert ReportSnapshot.objects.filter(review=review).count() == 1
