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
    path("<int:pk>/", views.MessageDetailView.as_view(), name="message_detail"),
]
