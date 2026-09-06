"""The school's own public enquiry page, scoped by its slug.

Mounted at the project root so the URL a school posts to Instagram reads as
theirs -- ``/fulfilled-academy/enquiry/`` -- rather than carrying the
platform's vocabulary in front of the school's name.

Two segments are always matched here, so nothing in this file can shadow the
single-segment public routes in :mod:`apps.core.urls` (``/``, ``/signup/``,
``/dashboard/``). It is nonetheless included *before* the core catch-all in
``config/urls.py``, because ordering that depends on nobody ever adding a
two-segment core route is ordering that will break quietly.
"""

from django.urls import path

from . import views

app_name = "admissions_public"

urlpatterns = [
    path(
        "<slug:school_slug>/enquiry/",
        views.PublicEnquiryView.as_view(),
        name="public_enquiry",
    ),
    path(
        "<slug:school_slug>/enquiry/received/",
        views.PublicEnquiryDoneView.as_view(),
        name="public_enquiry_done",
    ),
]
