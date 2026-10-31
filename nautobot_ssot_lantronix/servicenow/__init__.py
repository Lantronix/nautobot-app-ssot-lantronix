"""Optional ServiceNow console-path module.

Active only when nautobot-ssot's ServiceNow integration is enabled and its extras are installed.
Nothing here is imported at app load unless `unavailable_reason()` returns None, so core users need
no ServiceNow dependency.
"""

from __future__ import annotations


def unavailable_reason() -> str | None:
    """Why the console-path job can't register, or None when it can."""
    from django.conf import settings

    if not settings.PLUGINS_CONFIG.get("nautobot_ssot", {}).get("enable_servicenow"):
        return "nautobot-ssot's ServiceNow integration is not enabled (enable_servicenow)"
    try:
        import nautobot_ssot.integrations.servicenow.jobs  # noqa: F401
    except ImportError as exc:
        # nautobot-ssot itself only logs this at debug level, which hides a missing extra.
        return (
            f"nautobot-ssot's ServiceNow integration can't be imported ({type(exc).__name__}: "
            f"{exc}); install nautobot-app-ssot-lantronix[servicenow]"
        )
    return None
