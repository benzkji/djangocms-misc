from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

MIDDLEWARE_PATH = (
    "djangocms_misc.global_untranslated_placeholder.middleware"
    ".EditModeDefaultLanguageMiddleware"
)
TOOLBAR_MIDDLEWARE_PATH = "cms.middleware.toolbar.ToolbarMiddleware"


_toolbar_rebind_done = False


def _rebind_toolbar_module_url_helpers(**kwargs):
    global _toolbar_rebind_done
    if _toolbar_rebind_done:
        return
    _toolbar_rebind_done = True
    from cms.toolbar import toolbar as toolbar_module
    from cms.toolbar import utils as toolbar_utils

    toolbar_module.get_object_edit_url = toolbar_utils.get_object_edit_url
    toolbar_module.get_object_preview_url = toolbar_utils.get_object_preview_url
    toolbar_module.get_object_structure_url = toolbar_utils.get_object_structure_url


class GlobalUntranslatedPlaceholderConfig(AppConfig):
    name = "djangocms_misc.global_untranslated_placeholder"

    def ready(self):
        # cms.toolbar.toolbar imports get_object_{edit,preview,structure}_url
        # by name from cms.toolbar.utils. Our patch in models.py replaces
        # the names on cms.toolbar.utils at addon import-time. If
        # cms.toolbar.toolbar has already been imported by the time our
        # patch lands, its local references still point at the originals;
        # rebind them. Use Django's request_started signal because at
        # app-ready() time the order of apps may put us BEFORE cms.ready(),
        # which is what populates ``apps.get_app_config('cms').cms_extension``
        # — a module-level access in cms.toolbar.toolbar. Deferring to
        # request_started guarantees CMS's autodiscovery has finished.
        from django.core.signals import request_started

        request_started.connect(
            _rebind_toolbar_module_url_helpers,
            dispatch_uid="gup_rebind_toolbar_url_helpers",
        )

        # Only enforce the middleware contract when the addon is actually
        # enabled. When it's off (the default), no middleware is needed.
        if not getattr(settings, "DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS", None):
            return

        middleware = list(getattr(settings, "MIDDLEWARE", ()) or ())

        if MIDDLEWARE_PATH not in middleware:
            raise ImproperlyConfigured(
                "DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS is enabled but "
                f"{MIDDLEWARE_PATH!r} is not in MIDDLEWARE. Without it, edit "
                "URLs for non-default-language PageContent will not redirect "
                "to the default-language equivalent and plugin edits will land "
                f"on the wrong placeholders. Add it after {TOOLBAR_MIDDLEWARE_PATH!r}."
            )

        if TOOLBAR_MIDDLEWARE_PATH in middleware:
            # The redirect middleware reads request.toolbar, which is set by
            # the CMS ToolbarMiddleware. Misorder it and the redirect silently
            # never fires.
            toolbar_idx = middleware.index(TOOLBAR_MIDDLEWARE_PATH)
            redirect_idx = middleware.index(MIDDLEWARE_PATH)
            if redirect_idx < toolbar_idx:
                raise ImproperlyConfigured(
                    f"{MIDDLEWARE_PATH!r} must appear AFTER "
                    f"{TOOLBAR_MIDDLEWARE_PATH!r} in MIDDLEWARE."
                )
