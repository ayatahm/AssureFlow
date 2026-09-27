# AssureFlow

**Information Security Assessment & Assurance Platform** — a self-hosted Django + PostgreSQL application that takes a Security Review from intake to a frozen, versioned assessment report:

> Raise review → understand context → tailor assessment → provide evidence → InfoSec concludes → remediate or accept risk → issue report

The build follows `AssureFlow Implementation Specification v1.1`. This repository contains **only synthetic demo content**. Real rule sets, question wording, mappings, technical catalogues, evidence and review data are loaded locally from `private_data/` (gitignored) on an approved machine and must never be committed.

## What is here

| Area | Location |
|---|---|
| Settings, stage guard | `config/settings.py`, `apps/core/stage.py` |
| Accounts, sign-in (password → email code → forced change) | `apps/accounts/` |
| Central permissions `can(user, action, review, obj)` | `apps/core/permissions.py` |
| Versioned rule sets / technical catalogues + loader | `apps/content/` |
| Pure-Python applicability engine (no Django imports) | `apps/engine/engine.py` |
| Reviews: intake, triage, generation, questionnaire, scope | `apps/reviews/` |
| Evidence, conclusions, findings/actions, compliance, risk, acceptance, completion | `apps/assurance/` |
| Deterministic report snapshots (`af-json-v1`), Assurance Trace | `apps/reporting/` |
| Append-only audit log (+ PostgreSQL triggers) | `apps/audit/` |
| Restricted Django Admin allow-list, metadata-only assignments | `apps/administration/` |
| Synthetic demo rule set, catalogue, regression baseline | `seed_demo/` |
| Tools: baseline, reduction report, CSV extraction, backup/restore | `tools/` |
| Documentation | `docs/` |

## Quick start (local synthetic development)

Requirements: Python 3.11+, PostgreSQL 14+.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
createuser -P --createdb assureflow          # password e.g. assureflow
createdb -O assureflow assureflow
cp .env.example .env                         # set DB_PASSWORD; DJANGO_DEBUG=True for local work
python manage.py migrate
python manage.py seed_demo                   # synthetic content + demo accounts (local-synthetic/test only)
python manage.py runserver 127.0.0.1:8000
```

Sign in at <http://127.0.0.1:8000/> with a demo account (password `Demo-Password-2026!`):

| Role | Email |
|---|---|
| Requester | `sara.requester@demo.assureflow.local` |
| Reviewer | `layla.reviewer@demo.assureflow.local` (second: `yusuf.reviewer@…`) |
| Business Owner | `fatima.owner@demo.assureflow.local` |
| Admin | `admin@demo.assureflow.local` |

The default stage is **`EMAIL_CODE_REQUIRED`**. In development the code email is printed to the console (`EMAIL_MODE=console`) or written to `private_data/mail/` (`EMAIL_MODE=file`). `AUTH_FLOW_STAGE=PASSWORD_ONLY_LOCAL` exists only for local synthetic work: it requires `DEBUG=True`, the `local-synthetic` profile, loopback hosts, and shows a red banner on every page. Any other combination refuses to start (see `docs/security.md`).

## Tests

```bash
python -m pytest              # full suite (uses config/test_settings.py, EMAIL_CODE_REQUIRED)
python manage.py release_check  # release gate: fails on any failure OR any skipped test
```

Browser journeys in `tests/e2e` use Playwright with a local Chromium (`PLAYWRIGHT_CHROMIUM_EXECUTABLE` or `/opt/pw-browsers`). See `docs/testing.md` for what is covered and the latest recorded results.

## Loading approved private content

On an approved machine, with `AUTH_FLOW_STAGE=EMAIL_CODE_REQUIRED` and a non-synthetic profile:

```bash
python manage.py load_ruleset private_data/seed/ruleset.json --baseline private_data/baseline/baseline.json
python manage.py load_catalogue private_data/seed/catalogue.json
```

Loading non-synthetic content is refused in the password-only stage and in the `local-synthetic` profile. See `tools/extract_source.py` for the CSV → rule-set format.

## Operations

- `python manage.py verify_evidence` — recompute evidence hashes, report missing/modified files.
- `python manage.py verify_snapshots` — re-serialize stored report snapshots and compare digests.
- `tools/backup.sh` / `tools/restore.sh` — `pg_dump` + evidence archive, restore to a fresh database and verify.

See `docs/deployment.md` before any shared or production deployment.
