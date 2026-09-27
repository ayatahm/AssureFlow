from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrEmployeeIdBackend(ModelBackend):
    """Authenticate with registered email *or* employee ID plus password (spec 4.3)."""

    def authenticate(self, request, identifier=None, password=None, username=None, **kwargs):
        identifier = (identifier or username or kwargs.get("email") or "").strip()
        if not identifier or password is None:
            return None
        User = get_user_model()
        user = User.objects.filter(Q(email__iexact=identifier) | Q(employee_id=identifier)).first()
        if user is None:
            User().set_password(password)  # equalise timing for unknown accounts
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
