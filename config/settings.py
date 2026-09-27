"""AssureFlow settings.

Values come from environment variables (optionally a local ``.env`` file).
``validate_runtime_configuration`` at the bottom refuses unsafe combinations.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

from apps.core.stage import (
    EMAIL_CODE_REQUIRED,
    INSECURE_DEV_KEY_PREFIX,
    PROFILE_PRODUCTION,
    PROFILE_SHARED,
    validate_runtime_configuration,
)

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
ASSUREFLOW_PROFILE = env("ASSUREFLOW_PROFILE", "local-synthetic" if DEBUG else PROFILE_PRODUCTION)
AUTH_FLOW_STAGE = env("AUTH_FLOW_STAGE", EMAIL_CODE_REQUIRED)
ASSUREFLOW_RELEASE = env_bool("ASSUREFLOW_RELEASE", False)

SECRET_KEY = env("DJANGO_SECRET_KEY") or f"{INSECURE_DEV_KEY_PREFIX}-do-not-use-outside-local-development"
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")

INSTALLED_APPS = [
    # SimpleAdminConfig: no autodiscover. Only the explicit allow-list in
    # apps/administration/admin_site.py is exposed (spec 24.4).
    "django.contrib.admin.apps.SimpleAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_email",
    "apps.core",
    "apps.audit",
    "apps.accounts",
    "apps.content",
    "apps.reviews",
    "apps.assurance",
    "apps.reporting",
    "apps.administration",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.core.middleware.AuthStageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.shell",
            ],
        },
    }
]

if env("DB_ENGINE", "postgresql") == "sqlite":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "assureflow"),
            "USER": env("DB_USER", "assureflow"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "127.0.0.1"),
            "PORT": env("DB_PORT", "5432"),
            "ATOMIC_REQUESTS": False,
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailOrEmployeeIdBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = env("TIME_ZONE", "Asia/Bahrain")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Private evidence storage: outside static/media, never URL-routed (spec 9.1).
EVIDENCE_ROOT = Path(env("EVIDENCE_ROOT", str(BASE_DIR / "evidence"))).resolve()

# Sessions (idle/absolute limits come from SecuritySetting at request time).
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", True)
CSRF_COOKIE_HTTPONLY = True

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

if ASSUREFLOW_PROFILE in (PROFILE_SHARED, PROFILE_PRODUCTION):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if env_bool("BEHIND_TLS_PROXY") else None

# Email: development uses console/file backends; production uses the approved relay only.
EMAIL_MODE = env("EMAIL_MODE", "console" if DEBUG else "smtp")
if EMAIL_MODE == "console":
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
elif EMAIL_MODE == "file":
    EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
    EMAIL_FILE_PATH = BASE_DIR / "private_data" / "mail"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST", "")
    EMAIL_PORT = int(env("EMAIL_PORT", "25"))
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "assureflow@localhost")

OTP_EMAIL_SENDER = DEFAULT_FROM_EMAIL
OTP_EMAIL_SUBJECT = "AssureFlow sign-in code"
OTP_EMAIL_BODY_TEMPLATE_PATH = "accounts/email/code.txt"
OTP_EMAIL_TOKEN_VALIDITY = 300  # default; SecuritySetting controls the real value

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}

validate_runtime_configuration(
    stage=AUTH_FLOW_STAGE,
    profile=ASSUREFLOW_PROFILE,
    debug=DEBUG,
    allowed_hosts=ALLOWED_HOSTS,
    release_build=ASSUREFLOW_RELEASE,
    secret_key=SECRET_KEY,
)
