# coding: utf-8
from cms.models import Placeholder, PageContent  # noqa - needed, circular import otherwise
from cms.plugin_rendering import ContentRenderer  # , RenderedPlaceholder
from djangocms_versioning import constants

try:
    # cms 3.5 or 3.7+
    from cms.plugin_rendering import StructureRenderer
except ImportError:
    StructureRenderer = None


# load conf at startup
from .conf import UntranslatedPlaceholderConf  # noqa
# import signals at startup
from .signals import *  # noqa (will forget to update otherwise!)
from .utils import get_untranslated_default_language_if_enabled


def _get_placeholder_for_language(self, language, placeholder, page=None):
    default_placeholder = placeholder
    default_page = page
    if page:
        default_page = PageContent.admin_manager.filter(
            page=placeholder.source.page,
            language=language,
            versions__state=placeholder.source.versions.first().state,
        ).first()
        if default_page:
            default_placeholder = default_page.placeholders.filter(slot=placeholder.slot).first()
    return default_page, default_placeholder


def new_renderer__init__(self, request):
    self.__original_init__(request)
    lang = get_untranslated_default_language_if_enabled()
    if lang:
        self.request_language = lang


def new_content_render_placeholder(self, placeholder, context, language=None, page=None,
                           editable=False, use_cache=False, nodelist=None, width=None):
    language = language or self.request_language
    default_page, default_placeholder = self._get_placeholder_for_language(language, placeholder, page)
    return self.__original_render_placeholder(default_placeholder, context, language, default_page, editable, use_cache, nodelist, width)


# monkey patch!
# for normal plugin rendering.
ContentRenderer._get_placeholder_for_language = _get_placeholder_for_language
ContentRenderer.__original_init__ = ContentRenderer.__init__
ContentRenderer.__init__ = new_renderer__init__
ContentRenderer.__original_render_placeholder = ContentRenderer.render_placeholder
ContentRenderer.render_placeholder = new_content_render_placeholder


def new_structure_render_placeholder(self, placeholder, language, page=None):
    language = language or self.request_language
    default_page, default_placeholder = self._get_placeholder_for_language(language, placeholder, page)
    return self.__original_render_placeholder(default_placeholder, language, default_page)


if StructureRenderer:
    # for structure mode
    StructureRenderer._get_placeholder_for_language = _get_placeholder_for_language
    StructureRenderer.__original_init__ = StructureRenderer.__init__
    StructureRenderer.__init__ = new_renderer__init__
    StructureRenderer.__original_render_placeholder = StructureRenderer.render_placeholder
    StructureRenderer.render_placeholder = new_structure_render_placeholder
