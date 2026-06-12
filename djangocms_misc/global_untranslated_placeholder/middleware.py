from urllib.parse import urlparse

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
# right ``request_language`` and the rest of the flow (draft creation,
# redirect to ``get_editable_url``) lands on the right prefix.
VERSIONING_EDIT_REDIRECT_URL_SUFFIX = "_edit_redirect"

# djangocms-versioning's version-id-keyed admin actions whose post-action
# redirect URL is built from ``version.content.language`` via the helpers
# ``get_preview_url`` / ``get_object_live_url`` / ``version_list_url``. When
# the editor is editing the default-language sibling under a non-default
# URL prefix (e.g. ``/de/.../edit/<en_pk>/``), ``content.language`` is the
# default ('en'), so every redirect lands on ``/en/...`` — flipping the
# editor out of the language they were on.
#
# We can't redirect the inbound request itself: these endpoints are POST-only
# (admin.py:1104 returns 405 to GET) and a 302 would convert POST→GET. We
# fix it from the response side instead: when the response is a 3xx and
# its Location's leading language segment differs from the Referer's,
# rewrite the Location to use the Referer's language. The Referer is the
# source of truth — it tells us which language URL the editor was on when
# they clicked the action button.
VERSIONING_ACTION_URL_SUFFIXES = (
    "_publish",
    "_unpublish",
    "_revert",
    "_archive",
    "_discard",
)

_REDIRECT_STATUS_CODES = (301, 302, 303, 307, 308)


def _extract_leading_language(path, language_list):
    """Return the leading ``/<lang>/`` segment from a URL path as a language
    code, or ``None`` when the path has no recognised language prefix.

    ``language_list`` is the set of valid codes from
    ``cms.utils.i18n.get_language_list()``.
    """
    if not path:
        return None
    for code in language_list:
        if path.startswith(f"/{code}/") or path == f"/{code}":
            return code
    return None


def _swap_leading_language(path, new_lang, language_list):
    """Return ``path`` with its leading ``/<old_lang>/`` segment replaced by
    ``/<new_lang>/``. If ``path`` has no recognised leading language
    segment, return it unchanged.
    """
    for code in language_list:
        old_prefix = f"/{code}/"
        if path.startswith(old_prefix):
            return f"/{new_lang}/" + path[len(old_prefix) :]
    return path


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
        response = self.get_response(request)
        if get_untranslated_default_language_if_enabled():
            self._rewrite_action_redirect_language(request, response)
        return response

    def _rewrite_action_redirect_language(self, request, response):
        """When the response of a versioning admin action endpoint
        (``_publish`` / ``_unpublish`` / ``_revert`` / ``_archive`` /
        ``_discard``) is a redirect whose Location language prefix differs
        from the Referer's, rewrite Location's leading language segment to
        match the Referer.

        The Referer is the source of truth — it's the URL the editor was
        on when they POSTed the action. Versioning's post-action redirect
        targets are built from ``content.language``, which under this
        addon is always the default and so always wrong when the editor
        was on a non-default prefix.

        No-op when:
        - URL name doesn't match the suffixes
        - Response is not a 3xx
        - Location is missing or has no recognised language prefix
        - Referer is missing or has no recognised language prefix
        - Referer's language already matches Location's language
        """
        resolver_match = getattr(request, "resolver_match", None)
        if resolver_match is None:
            return
        url_name = resolver_match.url_name or ""
        if not url_name.endswith(VERSIONING_ACTION_URL_SUFFIXES):
            return
        if response.status_code not in _REDIRECT_STATUS_CODES:
            return
        location = response.get("Location", "")
        if not location:
            return

        from cms.utils.i18n import get_language_list

        language_list = list(get_language_list())

        location_path = urlparse(location).path
        location_lang = _extract_leading_language(location_path, language_list)
        if location_lang is None:
            return

        referer = request.headers.get("referer", "")
        referer_path = urlparse(referer).path if referer else ""
        intended_lang = _extract_leading_language(referer_path, language_list)
        if intended_lang is None or intended_lang == location_lang:
            return

        # Preserve any querystring / fragment carried on the Location URL.
        parsed = urlparse(location)
        new_path = _swap_leading_language(parsed.path, intended_lang, language_list)
        new_location = new_path
        if parsed.query:
            new_location = f"{new_location}?{parsed.query}"
        if parsed.fragment:
            new_location = f"{new_location}#{parsed.fragment}"
        response["Location"] = new_location

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

        if url_name in EDIT_URL_NAMES:
            return self._handle_placeholder_object_redirect(
                request, view_args, url_name, default_lang
            )

        return None

    def _handle_placeholder_object_redirect(
        self, request, view_args, url_name, default_lang
    ):
        """Redirect a placeholder edit/structure/preview URL that targets a
        non-default-language content object to the same URL with the
        default-language sibling's object_id swapped in. For edit and
        structure URLs a missing default-language DRAFT is auto-created
        from PUBLISHED; preview never creates drafts.
        """
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

        if url_name in EDITABLE_URL_NAMES:
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

        language_list = list(get_language_list())

        if content_language not in language_list:
            return None

        if request.path.startswith(f"/{content_language}/"):
            return None

        # Strip whatever the current leading language segment is and replace
        # it with /<content_language>/.
        new_path = _swap_leading_language(request.path, content_language, language_list)
        if new_path == request.path:
            return None
        query_string = request.META.get("QUERY_STRING")
        if query_string:
            new_path = f"{new_path}?{query_string}"
        return HttpResponseRedirect(new_path)
