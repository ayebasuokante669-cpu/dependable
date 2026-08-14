from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Dependable"
admin.site.site_title = "Dependable admin"
admin.site.index_title = "School management platform"

urlpatterns = [
    path("admin/", admin.site.urls),
    # Login, logout and password-change views under their conventional names.
    path("accounts/", include("django.contrib.auth.urls")),
    path("academics/", include("apps.academics.urls")),
    path("", include("apps.core.urls")),
]
