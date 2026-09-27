"""Build and issue immutable report snapshots (spec 18)."""
from django.utils import timezone

from apps.assurance.models import ActionState, EvidenceLink, Finding
from apps.assurance.services import status as st
from apps.reviews.models import Outcome, ParticipantRole

from .models import ReportSnapshot
from .queries import scope_summary
from .serialization import SERIALIZER_VERSION, iso_date, iso_utc, snapshot_sha256


def person(user):
    if user is None:
        return None
    return {"id": user.pk, "name": user.full_name, "employee_id": user.employee_id}


def evidence_entry(link):
    ev = link.evidence
    return {
        "evidence_id": ev.pk,
        "link_id": link.pk,
        "name": ev.original_name,
        "content_type": ev.content_type,
        "size": ev.size,
        "sha256": ev.sha256,
        "uploaded_by": person(ev.uploaded_by),
        "uploaded_at": iso_utc(ev.uploaded_at),
    }


def _links(qs):
    return [evidence_entry(link) for link in qs.select_related("evidence", "evidence__uploaded_by").order_by("evidence_id", "pk")]


def _acceptance_ref(acc, status):
    if acc is None:
        return {"status": status, "acceptance_id": None}
    return {"status": status, "status_label": st.TREATMENT_LABELS[status], "acceptance_id": acc.pk,
            "acceptance_code": acc.code}


def build_report_data(review, chk, *, issued_at, issued_by, revision):
    final_risk = chk.final_risk
    today = timezone.localdate(issued_at)
    treatments = {}
    for target, status, acc in chk.unresolved:
        key = ("rr" if hasattr(target, "applicability") else "ti", target.pk)
        treatments[key] = _acceptance_ref(acc, status)

    requirements = []
    for rr in review.requirements.select_related("control").order_by("order", "control__code", "pk"):
        questions = []
        for rq in review.questions.filter(question__in=rr.control.questions.all()).select_related(
            "question", "answered_by"
        ).order_by("question__order", "question__code"):
            questions.append({
                "code": rq.question.code, "text": rq.question.text, "answer": rq.answer or None,
                "comment": rq.comment, "answered_by": person(rq.answered_by),
                "answered_at": iso_utc(rq.answered_at), "on_behalf": rq.on_behalf,
            })
        current = st.current_conclusion(rr) if rr.is_applicable else None
        pcd = st.current_compliance(rr) if rr.is_applicable else None
        requirements.append({
            "id": rr.pk,
            "code": rr.control.code,
            "title": rr.control.title,
            "domain": rr.control.domain,
            "applicability": rr.applicability,
            "level": rr.level or None,
            "asked": rr.asked,
            "reason": rr.reason,
            "questions": questions if rr.is_applicable else [],
            "conclusion": None if current is None else {
                "status": current.status, "notes": current.notes, "decided_by": person(current.decided_by),
                "decided_at": iso_utc(current.decided_at),
            },
            "compliance_decision": None if pcd is None else {
                "decision": pcd.decision, "note": pcd.note, "decided_by": person(pcd.decided_by),
                "decided_at": iso_utc(pcd.decided_at),
            },
            "evidence": _links(EvidenceLink.objects.filter(review_requirement=rr)) if rr.is_applicable else [],
            "treatment": treatments.get(("rr", rr.pk)),
        })

    technical_items = []
    for item in review.technical_items.select_related("control").order_by("control__source_id", "pk"):
        technical_items.append({
            "id": item.pk, "source_id": item.control.source_id, "name": item.control.control_name,
            "category": item.control.raw_category, "included": item.included,
            "exclusion_reason": item.exclusion_reason, "added_by_reviewer": item.added_by_reviewer,
            "result": item.result or None, "notes": item.notes,
            "evidence": _links(EvidenceLink.objects.filter(technical_item=item)),
            "treatment": treatments.get(("ti", item.pk)),
        })

    findings = []
    for f in Finding.objects.filter(review=review).select_related(
        "review_requirement__control", "technical_item__control", "created_by"
    ).order_by("pk"):
        target = f.target
        origin = (
            {"type": "requirement", "id": target.pk, "code": target.control.code}
            if f.review_requirement_id else
            {"type": "technical_item", "id": target.pk, "code": target.control.source_id}
        )
        status, acc = st.target_treatment(review, target, final_risk, today)
        actions = []
        for a in f.actions.select_related("owner", "closed_by", "submitted_by").order_by("pk"):
            actions.append({
                "id": a.pk, "code": a.code, "owner": person(a.owner), "due_date": iso_date(a.due_date),
                "expected_outcome": a.expected_outcome, "state": a.state,
                "submission_note": a.submission_note, "submitted_by": person(a.submitted_by),
                "submitted_at": iso_utc(a.submitted_at), "closed_by": person(a.closed_by),
                "closed_at": iso_utc(a.closed_at),
                "alternative_treatment": _acceptance_ref(acc, status) if a.state == ActionState.OPEN else None,
                "history": [
                    {"kind": h.kind, "note": h.note, "actor": person(h.actor), "at": iso_utc(h.at),
                     "evidence_link_ids": sorted(h.evidence_link_ids)}
                    for h in a.history.select_related("actor").order_by("at", "pk")
                ],
                "evidence": _links(EvidenceLink.objects.filter(action=a)),
            })
        findings.append({
            "id": f.pk, "code": f.code, "title": f.title, "description": f.description, "priority": f.priority,
            "origin": origin, "created_by": person(f.created_by), "created_at": iso_utc(f.created_at),
            "actions": actions,
        })

    acceptances = []
    for acc in review.acceptances.select_related("business_owner", "created_by").order_by("created_at", "pk"):
        items = []
        for it in acc.items.select_related("review_requirement__control", "technical_item__control").order_by("pk"):
            if it.review_requirement_id:
                items.append({"type": "requirement", "id": it.review_requirement_id,
                              "code": it.review_requirement.control.code})
            else:
                items.append({"type": "technical_item", "id": it.technical_item_id,
                              "code": it.technical_item.control.source_id})
        acceptances.append({
            "id": acc.pk, "code": acc.code, "state": acc.state, "business_owner": person(acc.business_owner),
            "risk_record_id": acc.risk_record_id, "justification": acc.justification,
            "compensating_controls": acc.compensating_controls, "conditions": acc.conditions,
            "review_date": iso_date(acc.review_date), "expiry_date": iso_date(acc.expiry_date),
            "decision_note": acc.decision_note, "decided_at": iso_utc(acc.decided_at),
            "requested_by": person(acc.created_by), "requested_at": iso_utc(acc.created_at),
            "refers_to_final_risk": acc.risk_record_id == final_risk.pk,
            "valid_at_issue": acc.state == "ACCEPTED" and acc.risk_record_id == final_risk.pk
            and acc.expiry_date >= today,
            "items": items,
        })

    participants = sorted(
        ({"role": a.role, "user": person(a.user)} for a in review.assignments.select_related("user")),
        key=lambda p: (p["role"], p["user"]["id"]),
    )
    tags = sorted(
        ({"code": t.scope_tag.code, "label": t.scope_tag.label, "confirmed": not t.removed_at_confirmation}
         for t in review.scope_tags.select_related("scope_tag")),
        key=lambda t: t["code"],
    )
    rs, cat = review.rule_set, review.technical_catalogue
    return {
        "schema": SERIALIZER_VERSION,
        "report": {"revision": revision, "issued_at": iso_utc(issued_at), "issued_by": person(issued_by)},
        "review": {
            "id": review.pk, "reference": review.reference, "title": review.title, "purpose": review.purpose,
            "goals": review.goals, "department": review.department.name, "request_type": review.request_type,
            "request_type_label": review.get_request_type_display(), "project_owner": review.project_owner,
            "target_date": iso_date(review.target_date), "external_ref": review.external_ref,
            "classification": review.classification, "classification_reason": review.classification_reason,
            "routing": {k: bool(v) for k, v in sorted(review.routing.items())},
            "created_at": iso_utc(review.created_at), "created_by": person(review.created_by),
            "on_behalf": review.on_behalf, "generated_at": iso_utc(review.generated_at),
            "completed_at": iso_utc(review.completed_at), "participants": participants,
        },
        "sources": {
            "rule_set": {"version": rs.version, "label": rs.label, "source_sha256": rs.source_sha256,
                         "synthetic": rs.is_synthetic},
            "technical_catalogue": {"version": cat.version, "label": cat.label, "source_sha256": cat.source_sha256,
                                    "synthetic": cat.is_synthetic},
            "risk_scale": final_risk.scale_version,
        },
        "outcome": {"code": review.outcome,
                    "label": Outcome(review.outcome).label if review.outcome else "Outcome pending",
                    "summary": review.final_summary, "conditions": review.conditions},
        "scope_summary": scope_summary(review),
        "requirements": requirements,
        "technical": {
            "tags": tags, "confirmed_by": person(review.scope_confirmed_by),
            "confirmed_at": iso_utc(review.scope_confirmed_at), "confirmation_note": review.scope_confirmation_note,
            "items": technical_items,
        },
        "findings": findings,
        "risk": {
            "id": final_risk.pk, "inherent": final_risk.inherent, "residual": final_risk.residual,
            "rationale": final_risk.rationale, "scale_version": final_risk.scale_version,
            "scale_labels": list(final_risk.scale_labels), "assessed_by": person(final_risk.assessed_by),
            "assessed_at": iso_utc(final_risk.assessed_at),
        },
        "acceptances": acceptances,
    }


def issue_snapshot(actor, review, chk, *, issued_at):
    """Called inside the completion transaction. Core issues revision 1 once."""
    if ReportSnapshot.objects.filter(review=review).exists():
        raise ValueError("A report has already been issued for this review.")
    data = build_report_data(review, chk, issued_at=issued_at, issued_by=actor, revision=1)
    return ReportSnapshot.objects.create(
        review=review, revision=1, serializer_version=SERIALIZER_VERSION, data=data,
        sha256=snapshot_sha256(data), created_by=actor,
    )


def verify_snapshot(snapshot):
    """Re-serialize the *stored* data and compare to the recorded digest."""
    return snapshot_sha256(snapshot.data) == snapshot.sha256


def project_report(data, *, role, user_id=None):
    """Participant field projection of issued report data (spec 16.1)."""
    if role in (ParticipantRole.REVIEWER, ParticipantRole.REQUESTER):
        return data
    if role != ParticipantRole.BUSINESS_OWNER:
        return None
    mine = [a for a in data["acceptances"] if a["business_owner"] and a["business_owner"]["id"] == user_id]
    reqs = {i["id"] for a in mine for i in a["items"] if i["type"] == "requirement"}
    techs = {i["id"] for a in mine for i in a["items"] if i["type"] == "technical_item"}
    projected = dict(data)
    projected["requirements"] = [r for r in data["requirements"] if r["id"] in reqs]
    projected["technical"] = dict(data["technical"])
    projected["technical"]["items"] = [t for t in data["technical"]["items"] if t["id"] in techs]
    projected["findings"] = [
        f for f in data["findings"]
        if (f["origin"]["type"] == "requirement" and f["origin"]["id"] in reqs)
        or (f["origin"]["type"] == "technical_item" and f["origin"]["id"] in techs)
    ]
    projected["acceptances"] = mine
    projected["projection"] = "business_owner"
    return projected
