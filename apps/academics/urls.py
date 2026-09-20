from django.urls import path

from . import views

app_name = "academics"

urlpatterns = [
    path("classes/", views.ClassListView.as_view(), name="class_list"),
    path("classes/new/", views.ClassCreateView.as_view(), name="class_create"),
    # The table view of the same thing: a whole ladder in one submission.
    path(
        "classes/add-many/",
        views.ClassBulkCreateView.as_view(),
        name="class_bulk_create",
    ),
    # Every class at once, editable in place -- renaming and deactivating
    # without one page load per class.
    path(
        "classes/edit-all/",
        views.ClassEditAllView.as_view(),
        name="class_edit_all",
    ),
    path("classes/<int:pk>/edit/", views.ClassUpdateView.as_view(), name="class_update"),
    path(
        "classes/<int:pk>/delete/",
        views.ClassDeleteView.as_view(),
        name="class_delete",
    ),
    path("subjects/", views.SubjectListView.as_view(), name="subject_list"),
    path("subjects/new/", views.SubjectCreateView.as_view(), name="subject_create"),
    path(
        "subjects/add-many/",
        views.SubjectBulkCreateView.as_view(),
        name="subject_bulk_create",
    ),
    path(
        "subjects/<int:pk>/edit/",
        views.SubjectUpdateView.as_view(),
        name="subject_update",
    ),
    path(
        "subjects/<int:pk>/delete/",
        views.SubjectDeleteView.as_view(),
        name="subject_delete",
    ),
]
