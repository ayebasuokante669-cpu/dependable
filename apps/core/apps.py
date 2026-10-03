from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = "Core"

    def ready(self):
        # Importing the module is what registers the checks -- they are declared
        # with @register decorators. Here rather than at module scope because a
        # check may read settings and touch the app registry, neither of which is
        # ready while apps.py itself is being imported.
        from . import checks  # noqa: F401
