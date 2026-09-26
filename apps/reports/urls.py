from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.ReportsIndexView.as_view(), name="index"),
    # The same report the page above shows, as a file. Deliberately a URL of its
    # own rather than a ?format=pdf on the page: it is a different content type
    # with a different disposition, and a download that shares a URL with a
    # screen is one a browser will eventually cache as the other.
    path("collections.pdf", views.ReportPdfView.as_view(), name="pdf"),
]
