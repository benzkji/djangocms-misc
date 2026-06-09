from cms.plugin_rendering import ContentRenderer, StructureRenderer
from cms.toolbar import utils as toolbar_utils

from .conf import UntranslatedPlaceholderConf  # noqa: F401  load AppConf at startup
from .signals import *  # noqa: F403  wire up signal receivers at startup
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
    source = getattr(placeholder, "source", None)
    if source is None:
        return placeholder
    source_language = getattr(source, "language", None)
    default_lang = get_untranslated_default_language_if_enabled()
    if not default_lang or source_language is None or source_language == default_lang:
        return placeholder
    default_source = get_default_language_sibling(source, mirror_state_from=source)
    if default_source is None or default_source.pk == source.pk:
        return placeholder
    return (
        default_source.placeholders.filter(slot=placeholder.slot).first() or placeholder
    )


def _patch_content_renderer():
    original_init = ContentRenderer.__init__
    original_render_placeholder = ContentRenderer.render_placeholder

    def patched_init(self, request):
        original_init(self, request)
        default_lang = get_untranslated_default_language_if_enabled()
        if default_lang:
            self.request_language = default_lang

    def patched_render_placeholder(
        self,
        placeholder,
        context,
        language=None,
        page=None,
        editable=False,
        use_cache=False,
        nodelist=None,
        width=None,
    ):
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
            if hasattr(placeholder, "_plugins_cache"):
                del placeholder._plugins_cache
            if hasattr(placeholder, "_all_plugins_cache"):
                del placeholder._all_plugins_cache

        return original_render_placeholder(
            self,
            placeholder,
            context,
            language=language,
            page=page,
            editable=editable,
            use_cache=use_cache,
            nodelist=nodelist,
            width=width,
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
            if hasattr(placeholder, "_plugins_cache"):
                del placeholder._plugins_cache
            if hasattr(placeholder, "_all_plugins_cache"):
                del placeholder._all_plugins_cache
        return original_render_placeholder(self, placeholder, language, page=page)

    StructureRenderer.__init__ = patched_init
    StructureRenderer.render_placeholder = patched_render_placeholder


def _patch_toolbar_url_helpers():
    """
    cms.toolbar.utils.get_object_{edit,preview,structure}_url have a hard
    rule: ``language = getattr(obj, "language", language)  # Object trumps
    parameter``. So even though CMSToolbar passes ``language=self.request_
    language``, the URL is reversed under ``force_language(obj.language)``
    and the prefix becomes ``/<obj.language>/``.

    For the addon this is wrong: an editor on ``/de/`` editing the
    default-language sibling sees the structure board reload (via
    ``cms_edit_url`` in the toolbar context) land on ``/en/`` after a
    plugin save, dragging the entire admin out of the user's selected
    language. Worse, downstream ``cms_path`` queries on plugin-edit
    URLs then carry ``/en/`` too, breaking django-modeltranslation's
    language-tab logic.

    Wrap each helper: call the original to keep all of its concerns
    (live-url querystring, language list validation), then when our
    addon is enabled and the caller asked for a specific language
    that differs from the object's, rewrite the URL's leading language
    segment to match the requested language. This is gated on the
    addon so non-untranslated projects are unaffected.
    """
    from cms.utils.i18n import get_language_list

    def _patched(original):
        def wrapper(obj, language=None):
            url = original(obj, language=language)
            if not get_untranslated_default_language_if_enabled():
                return url
            if language is None:
                return url
            obj_lang = getattr(obj, "language", None)
            if obj_lang is None or obj_lang == language:
                return url
            if language not in get_language_list():
                return url
            old_prefix = f"/{obj_lang}/"
            new_prefix = f"/{language}/"
            if url.startswith(old_prefix):
                return new_prefix + url[len(old_prefix) :]
            return url

        return wrapper

    toolbar_utils.get_object_edit_url = _patched(toolbar_utils.get_object_edit_url)
    toolbar_utils.get_object_preview_url = _patched(
        toolbar_utils.get_object_preview_url
    )
    toolbar_utils.get_object_structure_url = _patched(
        toolbar_utils.get_object_structure_url
    )
    # cms.toolbar.toolbar imports these helpers by name (line 21 of
    # toolbar.py: ``from cms.toolbar.utils import get_object_edit_url, ...``)
    # so rebind them once that module is imported too. We can't import it
    # at addon load time because toolbar.toolbar's module-level code reads
    # ``apps.get_app_config('cms').cms_extension`` which isn't populated
    # until CMS app autodiscovery finishes. Defer via AppConfig.ready()
    # in apps.py.


def _patch_versioning_url_helpers():
    """
    djangocms_versioning.admin views build redirect URLs via two helpers
    in djangocms_versioning.helpers:

    - ``get_preview_url(content_obj, language=None)`` — used by
      ``publish_view`` and others. When ``language`` is not passed, the
      original falls back to ``content_obj.language`` — always the default
      ('en') under our addon, so the CMS-side patch above sees
      ``language == obj.language`` and skips the URL rewrite. Editor
      publishes from ``/de/...`` and lands on ``/en/.../preview/<en_pk>/``.

    - ``get_editable_url(content_obj, force_admin=False)`` — used by
      ``edit_redirect_view`` after creating a new draft. Always uses
      ``language = getattr(content_obj, "language", None)`` (no parameter
      to override). Same effect: after "Neuer Entwurf" on a /de/ preview
      the editor lands on ``/en/.../edit/<en_pk>/``.

    Patch both: when the addon is enabled and no explicit language is
    given, use the currently active request language (set by
    LocaleMiddleware from the URL prefix) instead of the content's
    language. Falls back cleanly to the original behaviour when the addon
    is off or djangocms-versioning is not installed.
    """
    if not apps.is_installed("djangocms_versioning"):
        return
    from django.utils.translation import get_language
    from djangocms_versioning import admin as versioning_admin
    from djangocms_versioning import helpers as versioning_helpers

    original_preview = versioning_helpers.get_preview_url
    original_editable = versioning_helpers.get_editable_url

    def patched_get_preview_url(content_obj, language=None):
        if language is None and get_untranslated_default_language_if_enabled():
            request_language = get_language()
            if request_language:
                language = request_language
        return original_preview(content_obj, language=language)

    def patched_get_editable_url(content_obj, force_admin=False):
        # ``get_editable_url`` has no ``language`` parameter; we instead
        # delegate to the patched CMS-side helper directly when the addon
        # is enabled and the request language differs from the content's,
        # so the URL rewrite kicks in. Otherwise fall back to the original.
        if get_untranslated_default_language_if_enabled():
            request_language = get_language()
            obj_language = getattr(content_obj, "language", None)
            if (
                request_language
                and obj_language
                and request_language != obj_language
                and not force_admin
            ):
                # Reuse the original helper's "editable model" branch by
                # calling get_object_edit_url with the request language.
                # The CMS-side patch then rewrites the URL prefix to
                # match. is_editable_model + the admin-fallback branch
                # of the original are preserved by the fall-through below
                # when those conditions don't hold.
                from cms.utils.helpers import is_editable_model

                if is_editable_model(content_obj.__class__):
                    return toolbar_utils.get_object_edit_url(
                        content_obj,
                        language=request_language,
                    )
        return original_editable(content_obj, force_admin=force_admin)

    versioning_helpers.get_preview_url = patched_get_preview_url
    versioning_helpers.get_editable_url = patched_get_editable_url
    # djangocms_versioning.admin imports both by name; rebind the local
    # references so publish_view + edit_redirect_view pick up the patches.
    versioning_admin.get_preview_url = patched_get_preview_url
    versioning_admin.get_editable_url = patched_get_editable_url


from django.apps import apps  # noqa: E402  (need this for the optional patch)

_patch_content_renderer()
_patch_structure_renderer()
_patch_toolbar_url_helpers()
_patch_versioning_url_helpers()
