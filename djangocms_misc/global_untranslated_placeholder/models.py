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


def _patch_content_renderer():
    original_init = ContentRenderer.__init__
    original_render_placeholder = ContentRenderer.render_placeholder

    def patched_init(self, request):
        original_init(self, request)
        default_lang = get_untranslated_default_language_if_enabled()
        if default_lang:
            self.request_language = default_lang

    def patched_render_placeholder(self, placeholder, context, language=None, page=None,
                                   editable=False, use_cache=False, nodelist=None,
                                   width=None):
        default_lang = get_untranslated_default_language_if_enabled()
        if default_lang:
            placeholder = _resolve_default_placeholder(placeholder)
            # Force the language even when the caller passed one explicitly
            # (e.g. {% render_placeholder foo language=... %}, apphook views,
            # cms_alias_tags). The swapped placeholder's plugins live under
            # the default language; filtering by anything else returns empty.
            language = default_lang
            # cms 4.1.11's render_page_placeholder preloads plugins with
            # get_language() (the request language) BEFORE we run, caching an
            # empty plugin list on this default-language placeholder via
            # _plugins_cache. The language override above can't un-poison it;
            # drop the cache so plugins are re-fetched under default_lang.
            # (Pre-4.1.11 the preload used self.request_language, which
            # patched_init already pins.)
            if hasattr(placeholder, '_plugins_cache'):
                del placeholder._plugins_cache
            if hasattr(placeholder, '_all_plugins_cache'):
                del placeholder._all_plugins_cache

        return original_render_placeholder(
            self, placeholder, context, language=language, page=page,
            editable=editable, use_cache=use_cache, nodelist=nodelist, width=width,
        )

    ContentRenderer.__init__ = patched_init
    ContentRenderer.render_placeholder = patched_render_placeholder


def _patch_structure_renderer():
    original_init = StructureRenderer.__init__
    original_render_placeholder = StructureRenderer.render_placeholder

    def patched_init(self, request):
        original_init(self, request)
        default_lang = get_untranslated_default_language_if_enabled()
        if default_lang:
            self.request_language = default_lang

    def patched_render_placeholder(self, placeholder, language, page=None):
        default_lang = get_untranslated_default_language_if_enabled()
        if default_lang:
            placeholder = _resolve_default_placeholder(placeholder)
            language = default_lang
            # Symmetric defense vs ContentRenderer's cache drop — see comment
            # there. Cheap and bounds the surprise window if a single request
            # touches both renderers for the same placeholder.
            if hasattr(placeholder, '_plugins_cache'):
                del placeholder._plugins_cache
            if hasattr(placeholder, '_all_plugins_cache'):
                del placeholder._all_plugins_cache
        return original_render_placeholder(self, placeholder, language, page=page)

    StructureRenderer.__init__ = patched_init
    StructureRenderer.render_placeholder = patched_render_placeholder


_patch_content_renderer()
_patch_structure_renderer()
