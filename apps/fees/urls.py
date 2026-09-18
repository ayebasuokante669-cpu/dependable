from django.urls import path

from . import views

app_name = "fees"

urlpatterns = [
    path("", views.FeeStructureListView.as_view(), name="structure_list"),
    path("terms/", views.TermListView.as_view(), name="term_list"),
    path("terms/new/", views.TermCreateView.as_view(), name="term_create"),
    path("terms/<int:pk>/edit/", views.TermUpdateView.as_view(), name="term_update"),
    path("new/", views.FeeStructureCreateView.as_view(), name="structure_create"),
    path(
        "<int:pk>/edit/",
        views.FeeStructureUpdateView.as_view(),
        name="structure_update",
    ),
    path(
        "<int:pk>/delete/",
        views.FeeStructureDeleteView.as_view(),
        name="structure_delete",
    ),
]
