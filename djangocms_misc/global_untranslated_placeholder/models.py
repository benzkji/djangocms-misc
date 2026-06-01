from cms.plugin_rendering import ContentRenderer, StructureRenderer

from .conf import UntranslatedPlaceholderConf  # noqa: F401  load AppConf at startup
from .signals import *  # noqa: F401,F403  wire up signal receivers at startup
from .utils import (
    get_default_language_sibling,
    get_untranslated_default_language_if_enabled,
)


def _resolve_default_placeholder(placeholder):
    """
    Swap a per-language Placeholder for the equivalent slot on the sibling
    content object that lives in the configured default language.

    Returns the original placeholder when the addon is disabled, when the
    source has no language field (e.g. static placeholders, plugin-on-plugin,
    or modeltranslation-style "single record" models), when the source is
    already the default language, or when no default-language sibling exists
    yet.
    """
    if placeholder is None:
        return placeholder
    source = getattr(placeholder, 'source', None)
    if source is None:
        return placeholder
    source_language = getattr(source, 'language', None)
    default_lang = get_untranslated_default_language_if_enabled()
    if not default_lang or source_language is None or source_language == default_lang:
        return placeholder
    default_source = get_default_language_sibling(source, mirror_state_from=source)
    if default_source is None or default_source.pk == source.pk:
        return placeholder
    return default_source.placeholders.filter(slot=placeholder.slot).first() or placeholder


def _patch_renderer(renderer_cls):
    original_init = renderer_cls.__init__
    original_render_placeholder = renderer_cls.render_placeholder

    def patched_init(self, request):
        original_init(self, request)
        default_lang = get_untranslated_default_language_if_enabled()
        if default_lang:
            self.request_language = default_lang

    def patched_render_placeholder(self, placeholder, *args, **kwargs):
        if get_untranslated_default_language_if_enabled():
            placeholder = _resolve_default_placeholder(placeholder)
        return original_render_placeholder(self, placeholder, *args, **kwargs)

    renderer_cls.__init__ = patched_init
    renderer_cls.render_placeholder = patched_render_placeholder


_patch_renderer(ContentRenderer)
_patch_renderer(StructureRenderer)
