"""The staff list, mounted at /staff/ rather than under /accounts/.

/accounts/ is the signed-out auth set -- login, password reset -- and robots.txt
disallows it wholesale. The staff roster is a signed-in school screen and has
nothing to do with authentication flows, so it gets its own prefix.
"""

from django.urls import path

from . import views

app_name = "staff"

urlpatterns = [
    path("", views.StaffListView.as_view(), name="list"),
]
