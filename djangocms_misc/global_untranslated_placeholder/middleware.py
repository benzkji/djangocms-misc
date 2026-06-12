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
# ``<app_label>_<modelversion>_<action>``. Their post-action redirect URLs
# are built from ``version.content.language`` (via ``get_editable_url`` /
# ``get_preview_url`` / ``get_object_live_url`` / ``version_list_url``).
# Under this addon the editor is always working on the default-language
# sibling (``content.language == 'en'``), so every one of those redirects
# lands on ``/en/...`` — flipping the editor out of whatever language URL
# they were actually on. (The action button URLs themselves can also carry
# the wrong prefix: the toolbar builds them via ``reverse()`` under
# ``force_language(toolbar_language)``, a user UI preference.)
#
# We can't 302 the inbound request to fix the prefix: all these endpoints
# are POST-only (admin.py returns 405 to GET) and a 302 would convert
# POST→GET. So we fix it from the response side instead: when the response
# is a 3xx and its Location's leading language segment differs from the
# HTTP Referer's, rewrite the Location to the Referer's language. The
# Referer is the single source of truth — it tells us which language URL
# the editor was on when they clicked the action button.
LANGUAGE_REDIRECT_URL_SUFFIXES = (
    "_edit_redirect",
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


def _referer_language(request, language_list):
    """The language the editor was on, read from the HTTP Referer's URL
    path. Returns ``None`` when the Referer is missing, unparseable, or
    its path has no recognised leading language segment.
    """
    referer = request.headers.get("referer", "")
    if not referer:
        return None
    return _extract_leading_language(urlparse(referer).path, language_list)


def _is_xhr(request):
    """True when the request was made by script (XHR / fetch), not a
    top-level browser navigation. jQuery (which the CMS frontend bundles)
    sets ``X-Requested-With: XMLHttpRequest``; modern browsers send
    ``Sec-Fetch-Mode`` on same-origin requests — anything other than
    ``navigate`` is a subresource/script request.
    """
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True
    sec_fetch_mode = request.headers.get("sec-fetch-mode")
    if sec_fetch_mode and sec_fetch_mode != "navigate":
        return True
    return False


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
            self._rewrite_redirect_location_language(request, response)
        return response

    def _rewrite_redirect_location_language(self, request, response):
        """When the response of a versioning admin action endpoint
        (``_edit_redirect`` / ``_publish`` / ``_unpublish`` / ``_revert`` /
        ``_archive`` / ``_discard``) is a redirect whose Location language
        prefix differs from the Referer's, rewrite Location's leading
        language segment to match the Referer.

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
        if not url_name.endswith(LANGUAGE_REDIRECT_URL_SUFFIXES):
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

        intended_lang = _referer_language(request, language_list)
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
        if url_name in EDIT_URL_NAMES:
            response = self._redirect_to_referer_language(request, url_name)
            if response is not None:
                return response
            return self._handle_placeholder_object_redirect(
                request, view_args, url_name, default_lang
            )

        return None

    def _redirect_to_referer_language(self, request, url_name):
        """Inbound counterpart of the Location rewrite, for the GET render
        endpoints (placeholder edit/structure/preview): when the request's
        URL prefix differs from the Referer's language, 302 to the same
        path under the Referer's language. Safe here because these are
        GET endpoints — no POST→GET method change.

        This catches toolbar-built URLs that carry the wrong prefix: the
        CMS helpers apply ``language = getattr(obj, "language", language)``
        (object trumps parameter), so on ``/de/.../edit/<en_pk>/`` the
        Preview button, the Structure/Content switcher, and the
        ``CMS.config`` ``edit``/``edit_off``/``structure`` URLs (used by
        the post-save structure-board XHR reload) all come out as
        ``/en/...``.

        Edit and structure URLs are only rewritten for XHR requests: the
        post-save reload is an XHR, while a top-level navigation to an
        edit URL may be deliberate. Preview URLs are rewritten for any
        request — the toolbar language menu (which links to other
        languages' preview URLs) consequently can't switch languages
        anymore, which is acceptable under this addon: all languages
        render the same default-language plugins anyway.
        """
        from cms.utils.i18n import get_language_list

        language_list = list(get_language_list())

        request_lang = _extract_leading_language(request.path, language_list)
        if request_lang is None:
            return None

        intended_lang = _referer_language(request, language_list)
        if intended_lang is None or intended_lang == request_lang:
            return None

        if url_name in EDITABLE_URL_NAMES and not _is_xhr(request):
            return None

        new_path = _swap_leading_language(request.path, intended_lang, language_list)
        if new_path == request.path:
            return None
        query_string = request.META.get("QUERY_STRING")
        if query_string:
            new_path = f"{new_path}?{query_string}"
        return HttpResponseRedirect(new_path)

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
