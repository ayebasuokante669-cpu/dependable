from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    # The log is the landing screen: "what have we told parents?" is asked far
    # more often than a message is written.
    path("", views.MessageListView.as_view(), name="index"),
    path("compose/", views.ComposeView.as_view(), name="compose"),
    # The core use case, one click from anywhere: fee reminders.
    path("reminders/", views.FeeReminderView.as_view(), name="fee_reminder"),
    path(
        "recipients/count/",
        views.RecipientCountView.as_view(),
        name="recipient_count",
    ),
    # Who this school sends as. Before <int:pk>, so "identity" is never read
    # as a message id.
    path("identity/", views.MessagingIdentityView.as_view(), name="identity"),
    path(
        "identity/<int:pk>/edit/",
        views.MessagingIdentityUpdateView.as_view(),
        name="identity_update",
    ),
    path("<int:pk>/", views.MessageDetailView.as_view(), name="message_detail"),
]
