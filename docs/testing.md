# Testing and recorded results

Run with `python -m pytest` (settings `config.test_settings`, stage `EMAIL_CODE_REQUIRED`, PostgreSQL). The release gate is `python manage.py release_check`: it forces `EMAIL_CODE_REQUIRED`, runs the full suite, and **fails on any failed or skipped test**.

| Folder | Covers |
|---|---|
| `tests/unit` | Engine (classification, gates, levels, dedup, missing answers, determinism, no Django imports); foundation (seed twice, Argon2id, audit immutability incl. DB trigger, security headers, no `|safe` on user content, no CDN, safe `next`) |
| `tests/auth` | Full sign-in: code required, single-use, expiry, concurrent use → one session, retry lockout, password lockout, resend cooldown, forced/expired password change, idle + absolute timeout, disabled user, session-version bump, `force_login` without code rejected, Admin route needs full flow, CSRF, no signup/impersonation; stage guard fail-closed combinations |
| `tests/permissions` | Visibility/404, SoD (own evidence/answers, own action, Reviewer≠Business Owner), Requester/BO/Admin projections, removed assignment/role, hidden-field tampering, Admin allow-list and forbidden routes, restricted user form, superuser hidden, role conflicts, assignment rules, metadata-only views |
| `tests/uploads` | Signature/size/policy checks, renamed executable, traversal filename, SHA-256, 0/2/3-target and duplicate links, cross-review requirement/action IDs, remediation evidence on Action + trace + audit provenance, private audited download, `verify_evidence` |
| `tests/workflow` | Lifecycle transitions allowed/refused, reset guards and version pinning, flags, return-to-requester re-review, every §15.3 completion example, risk authority, acceptance rules, snapshot determinism/immutability, concurrent completion, rollback on failure, full HTTP journeys for all roles |
| `tests/conformance` | Synthetic regression baseline (committed); approved private baseline from `private_data/baseline/` (skipped when absent) |
| `tests/e2e` | Playwright: Requester creates and submits; Reviewer concludes and completes; Business Owner accepts / declines — real email-code sign-in |

## Recorded run — 27 September 2026 (this build environment)

```
$ python -m pytest -q -rs
SKIPPED [1] tests/conformance/test_conformance.py:47: No approved private baseline in private_data/baseline/ (required for release conformance).
199 passed, 1 skipped in 94.83s

$ python manage.py release_check
199 passed, 1 skipped in 92.47s
CommandError: Release gate failed: 1 skipped test(s). Mandatory checks must run.

$ ASSUREFLOW_PROFILE=production DJANGO_DEBUG=False ... python manage.py check --deploy
?: (security.W021) You have not set the SECURE_HSTS_PRELOAD setting to True.
```

The release gate failure is expected and correct: conformance against the **approved private baseline** can only run on an approved machine with `private_data/` populated. It is not a passing control until that run is recorded. HSTS preload is intentionally not set for an internal hostname.

Backup/restore rehearsal (synthetic data): `tools/backup.sh` → `tools/restore.sh backups/<stamp> assureflow_restore_test <dir>` — checksums OK, `verify_evidence`: 1 file OK, `verify_snapshots`: 0 snapshots in that dev database. Repeat with completed reviews before handover.

## Not yet evidenced / open items

- Private-content conformance (needs approved `private_data/`).
- Production SMTP relay delivery and a production-like deployment rehearsal.
- Runtime DB role privilege revocation (documented in `deployment.md`; the trigger protection is tested).
- Malware scanning: not integrated.
- Should-have views (Posture, Accepted Risk Register) intentionally not built.
