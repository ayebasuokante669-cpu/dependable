"""Account routes that are ours rather than django.contrib.auth's."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("generate-password/", views.generate_password_view, name="generate_password"),
]
