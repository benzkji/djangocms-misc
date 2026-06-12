from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect

from .utils import (
    get_default_language_editable_sibling,
    get_default_language_sibling,
    get_untranslated_default_language_if_enabled,
)

# URL names that trigger an auto-create-draft redirect (the editor needs an
# editable target). When the default-language sibling has only a PUBLISHED
# version, a DRAFT is created from it before redirecting.
EDITABLE_URL_NAMES = {
    "cms_placeholder_render_object_edit",
    "cms_placeholder_render_object_structure",
}

# Preview is read-only. We still redirect to the default-language sibling
# when one exists with the matching state, but we never auto-create drafts
# as a side effect of viewing a preview URL.
PREVIEW_URL_NAMES = {
    "cms_placeholder_render_object_preview",
}

EDIT_URL_NAMES = EDITABLE_URL_NAMES | PREVIEW_URL_NAMES

# djangocms-versioning registers per-versionable admin URL names of the form
# ``<app_label>_<modelversion>_<action>``. The toolbar's Edit / Neuer Entwurf
# button builds its href via ``reverse()`` under ``force_language(toolbar_
# language)`` — which is the user's UI-language preference, NOT the URL
# prefix the editor is currently looking at. Result: editor on ``/de/`` whose
# user_settings.language is ``'en'`` sees a button URL on ``/en/...``, clicks
# it, and the whole flow flips to English.
#
# We catch this here: when the URL prefix of the incoming edit-redirect
# request doesn't match the language baked into the version's content
# (``version.content.language``), redirect to the same URL under the right
# prefix. From there, versioning's own ``edit_redirect_view`` runs under the
# right ``request_language`` and our patched ``get_editable_url`` lands the
# editor on ``/<right_prefix>/.../edit/<en_pk>/`` via the regular
# placeholder-edit middleware below.
VERSIONING_EDIT_REDIRECT_URL_SUFFIX = "_edit_redirect"


class EditModeDefaultLanguageMiddleware:
    """
    Redirects CMS frontend edit/structure/preview URLs for any non-default-language
    content object (PageContent or any other language-aware versionable) to the
    equivalent URL of the default-language sibling.

    With this middleware in place, every plugin add/move/copy/delete operation
    happens against the default-language content's placeholders, so the addon
    does not need to patch each PlaceholderAdmin endpoint individually.

    Must run after `cms.middleware.toolbar.ToolbarMiddleware`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        default_lang = get_untranslated_default_language_if_enabled()
        if not default_lang:
            return None

        resolver_match = getattr(request, "resolver_match", None)
        if resolver_match is None:
            return None

        url_name = resolver_match.url_name or ""
        if url_name.endswith(VERSIONING_EDIT_REDIRECT_URL_SUFFIX):
            return self._handle_versioning_edit_redirect(request, view_args)

        if url_name not in EDIT_URL_NAMES:
            return None

        try:
            content_type_id = int(view_args[0])
            object_id = int(view_args[1])
        except (IndexError, ValueError, TypeError):
            return None

        try:
            ct = ContentType.objects.get_for_id(content_type_id)
        except ContentType.DoesNotExist:
            return None

        model = ct.model_class()
        if model is None:
            return None

        manager = getattr(model, "admin_manager", model._base_manager)
        try:
            current = manager.get(pk=object_id)
        except ObjectDoesNotExist:
            return None

        if getattr(current, "language", None) is None:
            # Not a language-aware model (e.g. modeltranslation "single record").
            return None
        if current.language == default_lang:
            return None

        if resolver_match.url_name in EDITABLE_URL_NAMES:
            default_obj = get_default_language_editable_sibling(current, request.user)
        else:
            default_obj = get_default_language_sibling(
                current, mirror_state_from=current
            )
        if default_obj is None or default_obj.pk == current.pk:
            return None

        # Swap the object_id in-place in request.path instead of using reverse():
        # reverse() would re-prefix the URL with the default-language code, losing
        # the original language prefix that downstream consumers like
        # django-modeltranslation rely on (via the cms_path query) to pick the
        # correct translation tab. We want the URL prefix to stay e.g. /de/ while
        # the edited content object is the en one.
        old_segment = f"/{object_id}/"
        idx = request.path.rfind(old_segment)
        if idx == -1:
            return None
        new_path = request.path[:idx] + f"/{default_obj.pk}/"
        query_string = request.META.get("QUERY_STRING")
        if query_string:
            new_path = f"{new_path}?{query_string}"
        return HttpResponseRedirect(new_path)

    def _handle_versioning_edit_redirect(self, request, view_args):
        """When the toolbar's Edit / Neuer Entwurf button produced a URL on
        the wrong language prefix (because ``force_language(toolbar_language)``
        in ``_call_toolbar`` activated the user's UI-language preference
        instead of the URL-prefix language), redirect to the same URL under
        the language the version's content lives in.

        The Version's content has a ``language`` attribute — that's our
        ground truth. If ``request.path`` doesn't start with
        ``/<content.language>/``, rewrite the leading language segment.
        """
        try:
            version_id = int(view_args[0])
        except (IndexError, ValueError, TypeError):
            return None

        try:
            from djangocms_versioning.models import Version
        except ImportError:
            return None

        try:
            version = Version.objects.get(pk=version_id)
        except Version.DoesNotExist:
            return None

        content_language = getattr(version.content, "language", None)
        if not content_language:
            # Non-language-aware versionable — nothing to fix.
            return None

        from cms.utils.i18n import get_language_list

        if content_language not in get_language_list():
            return None

        expected_prefix = f"/{content_language}/"
        if request.path.startswith(expected_prefix):
            return None

        # Strip whatever the current leading language segment is and replace
        # it with /<content_language>/.
        for code in get_language_list():
            old_prefix = f"/{code}/"
            if request.path.startswith(old_prefix):
                new_path = expected_prefix + request.path[len(old_prefix) :]
                query_string = request.META.get("QUERY_STRING")
                if query_string:
                    new_path = f"{new_path}?{query_string}"
                return HttpResponseRedirect(new_path)
        return None
