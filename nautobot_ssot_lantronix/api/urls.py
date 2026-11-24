"""API routes, mounted by Nautobot at /api/plugins/ssot-lantronix/."""

from django.urls import path

from . import views

urlpatterns = [path("events/", views.EventsView.as_view(), name="events")]
