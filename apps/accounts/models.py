from datetime import timedelta

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django_otp.plugins.otp_email.models import EmailDevice


class Role:
    """The four application roles, stored as Django Groups (spec 3, 4.3)."""

    REQUESTER = "Requester"
    REVIEWER = "Reviewer"
    BUSINESS_OWNER = "Business Owner"
    ADMIN = "Admin"

    ALL = (REQUESTER, REVIEWER, BUSINESS_OWNER, ADMIN)
    BUSINESS = (REQUESTER, REVIEWER, BUSINESS_OWNER)


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, employee_id, password, **extra):
        if not email:
            raise ValueError("Email is required.")
        if not employee_id:
            raise ValueError("Employee ID is required.")
        user = self.model(email=self.normalize_email(email).lower(), employee_id=employee_id, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, employee_id, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, employee_id, password, **extra)

    def create_superuser(self, email, employee_id, password=None, **extra):
        """Technical installation/emergency account (spec 3.4) — not a business participant."""
        extra.setdefault("full_name", "Technical Administrator")
        extra["is_staff"] = True
        extra["is_superuser"] = True
        return self._create_user(email, employee_id, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    employee_id = models.CharField(max_length=32, unique=True)
    full_name = models.CharField(max_length=200)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, null=True, blank=True, related_name="users")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Technical flag for Django Admin access.")
    must_change_password = models.BooleanField(default=False)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    failed_logins = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    # Bumped whenever identity, status or roles change so existing sessions are invalidated.
    session_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["employee_id"]

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.employee_id})"

    def clean(self):
        super().clean()
        self.email = (self.email or "").strip().lower()

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def first_name(self):
        return (self.full_name or "").split(" ")[0]

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.first_name

    def get_session_auth_hash(self):
        """Include session_version so role/identity/status changes end sessions (spec 4.5)."""
        key_salt = "apps.accounts.models.User.get_session_auth_hash"
        return salted_hmac(key_salt, f"{self.password}:{self.session_version}", algorithm="sha256").hexdigest()

    # --- roles ---------------------------------------------------------
    @property
    def role_names(self):
        if not hasattr(self, "_af_roles"):
            self._af_roles = frozenset(self.groups.values_list("name", flat=True)) if self.pk else frozenset()
        return self._af_roles

    def clear_role_cache(self):
        self.__dict__.pop("_af_roles", None)

    def has_role(self, role):
        return role in self.role_names

    @property
    def is_app_admin(self):
        return Role.ADMIN in self.role_names

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    def password_expired(self, expiry_days):
        if not expiry_days:
            return False
        if not self.password_changed_at:
            return True
        return timezone.now() - self.password_changed_at > timedelta(days=expiry_days)


def _singleton_validator(model):
    def validate(instance):
        if model.objects.exclude(pk=instance.pk).exists():
            raise ValidationError("Only one settings record is allowed.")
    return validate


class SecuritySetting(models.Model):
    """Admin-editable authentication/session policy (spec 21.1). Singleton."""

    otp_lifetime_seconds = models.PositiveIntegerField(default=300, validators=[MinValueValidator(60)])
    otp_retry_limit = models.PositiveIntegerField(default=5, validators=[MinValueValidator(1)])
    otp_resend_cooldown_seconds = models.PositiveIntegerField(default=60, validators=[MinValueValidator(10)])
    lockout_threshold = models.PositiveIntegerField(default=5, validators=[MinValueValidator(1)])
    lockout_minutes = models.PositiveIntegerField(default=15, validators=[MinValueValidator(1)])
    idle_timeout_minutes = models.PositiveIntegerField(default=30, validators=[MinValueValidator(1)])
    max_session_minutes = models.PositiveIntegerField(default=480, validators=[MinValueValidator(5)])
    password_expiry_days = models.PositiveIntegerField(default=0, help_text="0 = expiry disabled.")
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "security setting"

    def __str__(self):
        return "Security settings"

    def clean(self):
        _singleton_validator(SecuritySetting)(self)
        if self.max_session_minutes < self.idle_timeout_minutes:
            raise ValidationError("Maximum session length must be at least the idle timeout.")

    @classmethod
    def current(cls):
        obj = cls.objects.order_by("pk").first()
        return obj or cls()


class SignInCodeDevice(EmailDevice):
    """django-otp EmailDevice whose cooldown/throttle values come from SecuritySetting.

    Codes are always sent to the account's registered email; the ``email``
    override field of EmailDevice is never populated.
    """

    class Meta:
        proxy = True

    def get_cooldown_duration(self):
        return SecuritySetting.current().otp_resend_cooldown_seconds

    def get_throttle_factor(self):
        return 1
