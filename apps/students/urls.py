from django.urls import path

from . import views

app_name = "students"

urlpatterns = [
    path("", views.StudentListView.as_view(), name="student_list"),
    path("new/", views.StudentCreateView.as_view(), name="student_create"),
    # Before the <int:pk> patterns, so "import" is never read as a student id.
    path("import/", views.StudentImportView.as_view(), name="student_import"),
    path(
        "import/template.xlsx",
        views.StudentImportTemplateView.as_view(),
        name="student_import_template",
    ),
    path(
        "import/review/",
        views.StudentImportReviewView.as_view(),
        name="student_import_review",
    ),
    path("<int:pk>/", views.StudentDetailView.as_view(), name="student_detail"),
    path("<int:pk>/edit/", views.StudentUpdateView.as_view(), name="student_update"),
    path("<int:pk>/delete/", views.StudentDeleteView.as_view(), name="student_delete"),
]
