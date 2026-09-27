# Deployment notes (bank-controlled infrastructure)

## Environment

```
DJANGO_DEBUG=False
ASSUREFLOW_PROFILE=production         # or 'shared' for a test server
AUTH_FLOW_STAGE=EMAIL_CODE_REQUIRED
DJANGO_SECRET_KEY=<≥50 random characters>
DJANGO_ALLOWED_HOSTS=assureflow.internal.example
EMAIL_MODE=smtp
EMAIL_HOST=<approved relay>  EMAIL_PORT=25  EMAIL_USE_TLS=True
EVIDENCE_ROOT=/srv/assureflow/evidence   # outside the web root; not served by the web server
BEHIND_TLS_PROXY=True                    # when TLS terminates at a reverse proxy
```

The settings module refuses to start with an unsafe combination. Run `python manage.py check --deploy` and `python manage.py collectstatic` (static files are local; no CDN). Serve with a WSGI server (e.g. gunicorn) behind the bank's reverse proxy. Never expose `EVIDENCE_ROOT` through the web server.

## Database roles

Use a migration/owner role for `migrate` and a separate runtime role for the application:

```sql
CREATE ROLE assureflow_app LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE assureflow TO assureflow_app;
GRANT USAGE ON SCHEMA public TO assureflow_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO assureflow_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO assureflow_app;
-- Append-only tables: no UPDATE/DELETE for the runtime role.
REVOKE UPDATE, DELETE ON audit_auditevent, reporting_reportsnapshot, assurance_conclusion,
  assurance_riskrecord, assurance_projectcompliancedecision, assurance_actionhistory FROM assureflow_app;
```

The migrations also install row triggers that reject UPDATE/DELETE on those tables. This is table-level hardening, not row-level security, and it does not protect against an operator with owner/superuser database access.

## Accounts

Create the technical superuser once (`python manage.py createsuperuser`); it must pass the full web sign-in flow and is not a business participant. Application Admins are ordinary accounts with the Admin role, created through `/admin/`. Never run `seed_demo` outside the local-synthetic/test profiles (it refuses to).

## Backup and restore

`tools/backup.sh` writes `pg_dump` (custom format) + an evidence archive + `SHA256SUMS`. `tools/restore.sh <dir> <new_db> <new_evidence_dir>` verifies checksums, restores into a fresh database, unpacks evidence and runs `verify_evidence` and `verify_snapshots`. After a restore, spot-check a review overview, its trace and its report.
