"""Account routes that are ours rather than django.contrib.auth's."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("settings/", views.AccountSettingsView.as_view(), name="settings"),
    path("generate-password/", views.generate_password_view, name="generate_password"),
]
