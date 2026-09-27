# Security controls

## Authentication (spec 4)

1. `POST /account/login/` — email **or** employee ID + password (Argon2id first in `PASSWORD_HASHERS`). Failures increment `failed_logins`; reaching `SecuritySetting.lockout_threshold` sets `locked_until`. A generic error is always shown.
2. A *pending* session is created (`request.session.flush()` first). `request.user` stays anonymous, so no protected route, download or Django Admin page is reachable.
3. A 6-digit code is generated with django-otp (`SignInCodeDevice`, a proxy of `EmailDevice`: `generate_is_allowed` / `cooldown_set` / `generate_token`) and emailed to the **registered** address only. Lifetime, retry limit and resend cooldown come from `SecuritySetting`.
4. `verify_code` row-locks the device (`select_for_update`) and calls `verify_token`, which clears the token on success — a code authenticates one session only (tested with concurrent requests). Exceeding the retry limit clears the token, locks the account and ends the pending login.
5. Temporary / reset / expired passwords must be changed before anything else.
6. `complete_login` calls Django `login` (session key rotated), `django_otp.login`, and starts the absolute timer.

`AuthStageMiddleware` then enforces on **every** request: completed sign-in flag, OTP-verified session (in `EMAIL_CODE_REQUIRED`), absolute and idle limits, and forced password change. Codes and passwords are never written to audit detail (`audit.services.record` rejects such keys).

## Stage guard (spec 4.7)

`apps/core/stage.py::validate_runtime_configuration` runs when settings load. `PASSWORD_ONLY_LOCAL` requires `DEBUG=True`, profile `local-synthetic`, loopback-only `ALLOWED_HOSTS`, and no release build; `runserver` additionally refuses non-loopback binding and non-synthetic content. Shared/production profiles and release builds require `DEBUG=False` and a strong secret key. Private content loading is refused in the password-only stage and in the local-synthetic profile.

## Authorisation (spec 22)

- `visible_reviews(user)` — only reviews with an assignment whose participant role matches the account's current application role. Admin and technical superuser accounts see none.
- `can(user, action, review, obj)` — one table of rules (`RULES`) checking role, assignment, state, same-review ownership and separation of duties. Unknown actions → `False`.
- Separation of duties: a Reviewer cannot record *Verified* where they uploaded linked evidence or authored a linked answer; nobody closes an action they own or submitted; a Reviewer on the review cannot be its Business Owner; Admin cannot hold business roles, self-assign, or change their own roles.
- Unauthorised review IDs return 404; visible-but-not-permitted actions return 403.

## Django Admin (spec 3.4, 24.4)

A dedicated `AdminSite` (`/admin/`) with `SimpleAdminConfig` (no autodiscover) registers only Department, User (restricted form: no `is_superuser`, permissions, groups editor or OTP data; technical superuser hidden), SecuritySetting, EvidencePolicy and read-only RuleSet/TechnicalCatalogue metadata. Admin login/password-change routes redirect to the full AssureFlow sign-in. Participant assignment uses `/administration/review-assignments/` with an explicit metadata projection.

## Evidence (spec 9)

Authorisation happens before any bytes are written. Size is checked against `EvidencePolicy`, the type is taken from the file signature (PDF/PNG/JPEG), the file is stored under a random UUID name in `EVIDENCE_ROOT` (outside static/media, never URL-routed) while computing SHA-256, and the original name is kept as display metadata only (path components stripped). Downloads go through an authorised, audited view with `as_attachment=True`. **No malware scanner is integrated.**

## HTTP hardening

CSRF middleware; `X-Frame-Options: DENY`; `nosniff`; `Referrer-Policy: same-origin`; HTTP-only cookies; secure cookies, HSTS and SSL redirect in shared/production profiles; auto-escaping everywhere (the only `|safe` is on developer-defined form help text); no raw SQL built from strings; local static assets only (no CDN).
