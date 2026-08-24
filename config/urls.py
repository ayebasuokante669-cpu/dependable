from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core.views import RoleAwareLoginView

admin.site.site_header = "Dependable"
admin.site.site_title = "Dependable admin"
admin.site.index_title = "School management platform"

urlpatterns = [
    path("admin/", admin.site.urls),
    # Ours, before the include below, so this is the "login" that gets reversed.
    # Everything else in the auth set -- logout, and the whole password-reset
    # and password-change flow -- is Django's, driven by the templates in
    # templates/registration/.
    path("accounts/login/", RoleAwareLoginView.as_view(), name="login"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("academics/", include("apps.academics.urls")),
    path("fees/", include("apps.fees.urls")),
    path("students/", include("apps.students.urls")),
    path("messaging/", include("apps.messaging.urls")),
    path("payments/", include("apps.payments.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    # Uploaded receipts. In production these are served by the web server or
    # object storage; Django deliberately refuses to do it with DEBUG off.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
