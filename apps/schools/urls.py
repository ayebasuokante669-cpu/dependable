from django.urls import path

from . import views

app_name = "schools"

urlpatterns = [
    path("", views.BranchListView.as_view(), name="branch_list"),
    path("new/", views.BranchCreateView.as_view(), name="branch_create"),
    path("<int:pk>/edit/", views.BranchUpdateView.as_view(), name="branch_update"),
]
