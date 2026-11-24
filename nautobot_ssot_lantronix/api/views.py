"""Signed Percepxion events (webhook-payload v1).

No Nautobot login or token: the HMAC signature, with a secret from ``webhook_secrets``, is the
authentication. A verified port or device event for the configured organization requests a
focused sync of that console server, run as ``sync_user``.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import settings as app_settings
from ..jobs import focused_job_model, request_sync, sync_requests
from ..percepxion.events import MAX_BODY, EventRejected, parse, verify

log = logging.getLogger("nautobot_ssot_lantronix.events")


def _events_setup():
    """(config, sync user) when events are fully set up, else None: secrets, an active sync
    user and an enabled focused sync job. Each gap is logged, never echoed to the caller."""
    try:
        cfg = app_settings.load(settings.PLUGINS_CONFIG.get("nautobot_ssot_lantronix", {}))
    except app_settings.ConfigError as exc:
        log.warning("Events refused: Lantronix SSoT settings: %s", exc)
        return None
    if not cfg.webhook_secrets:
        return None
    user = (
        get_user_model().objects.filter(username=cfg.sync_user, is_active=True).first()
        if cfg.sync_user
        else None
    )
    if user is None:
        log.warning("Events refused: sync_user (PERCEPXION_SYNC_USER) is unset or not a user")
        return None
    job_model = focused_job_model()
    if job_model is None or not job_model.enabled:
        log.warning("Events refused: enable the Percepxion focused sync job")
        return None
    return cfg, user


class EventsView(APIView):
    authentication_classes = ()
    permission_classes = (permissions.AllowAny,)
    parser_classes = ()  # the signature covers the raw body, so it is never parsed here

    def post(self, request):
        setup = _events_setup()
        if setup is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        cfg, user = setup
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            return Response({"detail": "Bad body length."}, status=status.HTTP_400_BAD_REQUEST)
        body = request.body
        try:
            verify(cfg.webhook_secrets, request.headers, body)
        except EventRejected as exc:
            log.warning("Event refused: %s", exc)
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            event = parse(body)
        except EventRejected as exc:
            log.warning("Event refused: %s", exc)
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if event is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        if event.organization_id != cfg.organization_id:
            log.warning("Event refused: another organization")
            return Response(
                {"detail": "Event is for another organization."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sync_requests().seen(event.id):
            return Response(status=status.HTTP_204_NO_CONTENT)
        request_sync([event.device_id], user)
        return Response({"detail": "Sync requested."}, status=status.HTTP_202_ACCEPTED)
