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
    'cms_placeholder_render_object_edit',
    'cms_placeholder_render_object_structure',
}

# Preview is read-only. We still redirect to the default-language sibling
# when one exists with the matching state, but we never auto-create drafts
# as a side effect of viewing a preview URL.
PREVIEW_URL_NAMES = {
    'cms_placeholder_render_object_preview',
}

EDIT_URL_NAMES = EDITABLE_URL_NAMES | PREVIEW_URL_NAMES


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

        resolver_match = getattr(request, 'resolver_match', None)
        if resolver_match is None or resolver_match.url_name not in EDIT_URL_NAMES:
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

        manager = getattr(model, 'admin_manager', model._base_manager)
        try:
            current = manager.get(pk=object_id)
        except ObjectDoesNotExist:
            return None

        if getattr(current, 'language', None) is None:
            # Not a language-aware model (e.g. modeltranslation "single record").
            return None
        if current.language == default_lang:
            return None

        if resolver_match.url_name in EDITABLE_URL_NAMES:
            default_obj = get_default_language_editable_sibling(current, request.user)
        else:
            default_obj = get_default_language_sibling(current, mirror_state_from=current)
        if default_obj is None or default_obj.pk == current.pk:
            return None

        # Swap the object_id in-place in request.path instead of using reverse():
        # reverse() would re-prefix the URL with the default-language code, losing
        # the original language prefix that downstream consumers like
        # django-modeltranslation rely on (via the cms_path query) to pick the
        # correct translation tab. We want the URL prefix to stay e.g. /de/ while
        # the edited content object is the en one.
        old_segment = f'/{object_id}/'
        idx = request.path.rfind(old_segment)
        if idx == -1:
            return None
        new_path = request.path[:idx] + f'/{default_obj.pk}/'
        query_string = request.META.get('QUERY_STRING')
        if query_string:
            new_path = f'{new_path}?{query_string}'
        return HttpResponseRedirect(new_path)
