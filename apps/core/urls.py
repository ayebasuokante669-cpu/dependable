from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    # Public.
    path("", views.LandingView.as_view(), name="landing"),
    path("signup/", views.SignupView.as_view(), name="signup"),

    # "The dashboard", for anything that should not have to know the role.
    path("dashboard/", views.dashboard, name="dashboard"),

    # One per role -- see navigation.ROLE_HOME, which decides who lands where.
    path("platform/", views.PlatformOverviewView.as_view(), name="platform_overview"),
    path("school/", views.SchoolDashboardView.as_view(), name="school_dashboard"),
    path("branch/", views.BranchDashboardView.as_view(), name="branch_dashboard"),
    path("finance/", views.BursarDashboardView.as_view(), name="bursar_dashboard"),

    path("welcome/", views.OnboardingView.as_view(), name="onboarding"),
    # The school's own profile -- name, logo, office contacts. What the
    # "Settings" nav entry has been waiting for.
    path("settings/", views.SchoolSettingsView.as_view(), name="school_settings"),
]
