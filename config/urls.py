from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core.branding import PRODUCT_NAME
from apps.core.views import BrandedPasswordResetView, RoleAwareLoginView

admin.site.site_header = PRODUCT_NAME
admin.site.site_title = f"{PRODUCT_NAME} admin"
admin.site.index_title = "School management platform"

urlpatterns = [
    path("admin/", admin.site.urls),
    # Ours, before the include below, so this is the "login" that gets reversed.
    # Everything else in the auth set -- logout, and the whole password-reset
    # and password-change flow -- is Django's, driven by the templates in
    # templates/registration/.
    path("accounts/login/", RoleAwareLoginView.as_view(), name="login"),
    # Also ours, and for the same reason: the reset *email* is rendered without
    # a request, so context processors do not run and the product name has to
    # be handed in explicitly. See BrandedPasswordResetView.
    path(
        "accounts/password_reset/",
        BrandedPasswordResetView.as_view(),
        name="password_reset",
    ),
    # Ours, and before the catch-all auth include so the name resolves here.
    path("accounts/", include("apps.accounts.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("academics/", include("apps.academics.urls")),
    path("fees/", include("apps.fees.urls")),
    path("students/", include("apps.students.urls")),
    path("messaging/", include("apps.messaging.urls")),
    path("payments/", include("apps.payments.urls")),
    path("admissions/", include("apps.admissions.urls")),
    # The school's own public enquiry page, at /<school-slug>/enquiry/. Before
    # the core include so a two-segment public route can never be shadowed by a
    # catch-all added there later.
    path("", include("apps.admissions.public_urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    # Uploaded receipts. In production these are served by the web server or
    # object storage; Django deliberately refuses to do it with DEBUG off.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
