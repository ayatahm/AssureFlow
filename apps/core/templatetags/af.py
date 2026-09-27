from django import template
from django.utils.html import format_html

from apps.core.permissions import can as can_check

register = template.Library()

TONES = {
    # review state (spec 26)
    "DRAFT": "grey", "IN_ASSESSMENT": "blue", "IN_REVIEW": "navy", "AWAITING_ACCEPTANCE": "amber",
    "COMPLETED": "slate", "WITHDRAWN": "dark",
    # outcome
    "MET": "green", "MET_WITH_EXCEPTIONS": "amber", "NOT_MET": "red",
    # conclusion
    "VERIFIED": "green", "EVIDENCE_REQUIRED": "amber", "SECURITY_GAP": "red", "NOT_APPLICABLE": "grey",
    # levels
    "REQUIRED": "red-tint", "RECOMMENDED": "gold-tint", "OPTIONAL": "blue-tint",
    # risk / priority
    "LOW": "green", "MODERATE": "gold", "HIGH": "orange", "CRITICAL": "red",
    # answers
    "YES": "green", "NO": "red", "UNSURE": "amber",
    # flags
    "POTENTIAL_GAP": "red", "CLARIFICATION": "amber", "CONFIRM_NA": "grey",
    # action / acceptance
    "OPEN": "amber", "SUBMITTED": "blue", "CLOSED": "green",
    "PENDING": "amber", "ACCEPTED": "green", "DECLINED": "red", "EXPIRED": "red", "SUPERSEDED": "grey",
    "MISSING": "grey",
    # applicability / classification
    "KEEP": "navy", "SKIP": "grey",
    "PUBLIC": "grey", "SENSITIVE": "blue", "CONFIDENTIAL": "gold", "SECRET": "red",
}

LABELS = {
    "DRAFT": "Draft", "IN_ASSESSMENT": "In Assessment", "IN_REVIEW": "In Review",
    "AWAITING_ACCEPTANCE": "Awaiting Acceptance", "COMPLETED": "Completed", "WITHDRAWN": "Withdrawn",
    "MET": "Requirements met", "MET_WITH_EXCEPTIONS": "Met with accepted exceptions", "NOT_MET": "Requirements not met",
    "VERIFIED": "Verified", "EVIDENCE_REQUIRED": "Evidence Required", "SECURITY_GAP": "Security Gap",
    "NOT_APPLICABLE": "Not Applicable", "REQUIRED": "Required", "RECOMMENDED": "Recommended", "OPTIONAL": "Optional",
    "LOW": "Low", "MODERATE": "Moderate", "HIGH": "High", "CRITICAL": "Critical",
    "YES": "Yes", "NO": "No", "UNSURE": "Unsure",
    "POTENTIAL_GAP": "Potential Security Gap", "CLARIFICATION": "Clarification / Evidence Required",
    "CONFIRM_NA": "Reviewer confirmation required",
    "OPEN": "Open", "SUBMITTED": "Submitted", "CLOSED": "Closed",
    "PENDING": "Pending", "ACCEPTED": "Accepted", "DECLINED": "Declined", "EXPIRED": "Expired",
    "SUPERSEDED": "Superseded", "MISSING": "No decision",
    "KEEP": "Keep", "SKIP": "Skip",
    "PUBLIC": "Public", "SENSITIVE": "Sensitive", "CONFIDENTIAL": "Confidential", "SECRET": "Secret",
    "WILL_COMPLY": "Will Comply", "WILL_NOT_COMPLY": "Will Not Comply", "EXCEPTION_GRANTED": "Exception Granted",
}


@register.simple_tag
def badge(value, label=None):
    if not value:
        return format_html('<span class="badge badge-muted">{}</span>', label or "—")
    tone = TONES.get(value, "grey")
    return format_html('<span class="badge badge-{}">{}</span>', tone, label or LABELS.get(value, value))


@register.filter
def label(value):
    return LABELS.get(value, value)


@register.simple_tag
def can_do(user, action, review, obj=None):
    return can_check(user, action, review, obj)


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def filesize(num):
    try:
        num = int(num)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


@register.filter
def short_hash(value):
    return f"{value[:12]}…{value[-6:]}" if value and len(value) > 20 else value
