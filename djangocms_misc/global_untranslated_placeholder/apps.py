from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


MIDDLEWARE_PATH = (
    'djangocms_misc.global_untranslated_placeholder.middleware'
    '.EditModeDefaultLanguageMiddleware'
)
TOOLBAR_MIDDLEWARE_PATH = 'cms.middleware.toolbar.ToolbarMiddleware'


class GlobalUntranslatedPlaceholderConfig(AppConfig):
    name = 'djangocms_misc.global_untranslated_placeholder'

    def ready(self):
        # Only enforce the middleware contract when the addon is actually
        # enabled. When it's off (the default), no middleware is needed.
        if not getattr(settings, 'DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS', None):
            return

        middleware = list(getattr(settings, 'MIDDLEWARE', ()) or ())

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
