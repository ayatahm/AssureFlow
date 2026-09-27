from django.conf import settings

from apps.accounts.models import Role
from apps.core.stage import PASSWORD_ONLY_BANNER, is_password_only


def shell(request):
    user = getattr(request, "user", None)
    roles = []
    if user is not None and user.is_authenticated:
        roles = sorted(user.role_names)
    return {
        "af_password_only_banner": PASSWORD_ONLY_BANNER if is_password_only(settings) else "",
        "af_roles": roles,
        "af_is_requester": Role.REQUESTER in roles,
        "af_is_reviewer": Role.REVIEWER in roles,
        "af_is_business_owner": Role.BUSINESS_OWNER in roles,
        "af_is_admin": Role.ADMIN in roles or bool(user and getattr(user, "is_superuser", False)),
        "af_profile": settings.ASSUREFLOW_PROFILE,
    }
