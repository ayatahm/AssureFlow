# Architecture and implementation decisions

## Layering

Views are thin: they load the review through `get_visible_review_or_404`, check `can(...)`, call a **service**, and render. Every state change lives in a service function under `apps/*/services/`, runs inside `transaction.atomic`, re-reads and row-locks the review (`select_for_update`), re-checks permission with the same `can()` used by views, and writes its `AuditEvent` in the same transaction.

The engine (`apps/engine/engine.py`) is pure Python with no Django imports. `apps/content/loader.py` converts a pinned `RuleSet` into the plain dict the engine consumes.

## Engine configuration

Organisation logic is configuration, not code:

- `classification.rules` — ordered, first match wins, else `default`.
- `routing` — named indicators, each a condition.
- `gates` — `skip_when` condition; a control listing a gate is **Skipped** when it fires.
- `controls[].levels` — requirement level per classification.
- `controls[].questions` — links to assessment questions (many-to-many).
- `always_ask` — questions presented in every assessment.

Conditions: `{"q","eq"}`, `{"q","in"}`, `{"all":[]}`, `{"any":[]}`, `{"not":…}`.

Question selection: Required/Recommended kept controls drive their questions; Optional kept controls do not force a question; Skipped controls never do; a question linked from several controls is shown once (all links kept for traceability). `ReviewRequirement.asked` means at least one linked question was presented.

## v1.1 schema decisions (reviewed before the first migration)

| Point | Implementation |
|---|---|
| Custom user | `accounts.User` (`AbstractBaseUser`), `USERNAME_FIELD="email"`, unique `employee_id`, lower-cased email, `session_version` included in the session auth hash so role/identity/status changes end sessions. |
| Roles | Four Django Groups created by data migration `accounts.0002`. |
| EvidenceLink | Three nullable targets (`review_requirement`, `technical_item`, `action`) with a DB `CheckConstraint` for exactly one, and partial unique constraints per evidence/target. Same-review ownership enforced in `evidence.resolve_target` + `can("evidence.upload")`. |
| Finding | Exactly one of requirement / technical item (DB check). |
| RiskRecord | Append-only; latest by (`assessed_at`, pk) is current. `Review.final_risk_record` is set only by `complete_review`. No editable residual-risk column on Review. |
| Acceptance | `BusinessRiskAcceptance.risk_record` pins the presented RiskRecord; items target a requirement **or** a technical item (DB check). The latest request per target is authoritative. |
| Snapshot | `ReportSnapshot` unique `(review, revision)`, `serializer_version="af-json-v1"`, SHA-256 of the canonical bytes. |
| Append-only tables | `AuditEvent`, `ReportSnapshot`, `Conclusion`, `RiskRecord`, `ProjectComplianceDecision`, `ActionHistory`: model/queryset guards plus a PostgreSQL trigger rejecting UPDATE/DELETE (`audit.0002`). |

## Derived, never stored

- Assessment Scope Summary counts (`reporting.queries.scope_summary`).
- Current conclusion: the latest `Conclusion`, **unless** a linked answer changed after it — then it is history and the requirement needs re-review.
- Treatment status of a gap / technical finding / Open action (`assurance.services.status.treatment_status`): `MISSING`, `PENDING`, `SUPERSEDED` (older risk record), `EXPIRED`, `ACCEPTED`, `DECLINED`.
- Neutral outcome at completion: *Requirements met* / *Met with accepted exceptions* / *Requirements not met*.

## Completion (`apps/assurance/services/completion.py`)

`evaluate_completion` implements every guard of spec §15.1 and returns an explicit checklist and blocker list. `complete_review` locks the review, re-evaluates, pins the final RiskRecord, sets the outcome, issues snapshot revision 1 and audits — all in one transaction. A second concurrent completion waits on the row lock, then fails the state check; the unique constraint is the backstop.

## Report snapshot

`reporting.services.build_report_data` builds JSON-only data (timestamps as UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`, dates ISO, collections sorted by stable keys). `serialization.snapshot_bytes` validates types (rejects floats, sets, tuples, datetimes, non-string keys) and serializes with `sort_keys`, compact separators, `ensure_ascii=False`, `allow_nan=False`, UTF-8. Reports are rendered from stored data only, with a participant projection (the Business Owner sees only the items covered by their decisions). The digest is not a signature.

## Assurance Trace

`reporting.queries.build_trace` assembles the six trace sections from existing records (no parallel workflow), including remediation evidence reached through `Action.finding`, and uses the pinned RiskRecord on completed reviews. Field projection by viewer: Reviewer full (history, flags, framework references); Requester recorded conclusions/findings/treatments; Business Owner only for covered requirements; Admin/unassigned 404.
