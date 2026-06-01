from django.apps import apps
from django.conf import settings


def get_untranslated_default_language_if_enabled():
    """
    Returns the language code in which all plugins should be stored / rendered
    when the addon is enabled, or None if the addon is disabled.

    Setting can be:
      - False/None/missing: addon disabled
      - True: use settings.LANGUAGE_CODE
      - a language code string ('en', 'de', ...): use that language if it
        appears in settings.LANGUAGES, otherwise fall back to LANGUAGE_CODE
    """
    value = getattr(settings, 'DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS', None)
    if value and value is not True:
        for lang_tuple in settings.LANGUAGES:
            if lang_tuple[0] == value:
                return value
    if value:
        return settings.LANGUAGE_CODE
    return None


def _versioning_installed():
    return apps.is_installed('djangocms_versioning')


def get_versionable_for(instance_or_model):
    """
    Return the djangocms-versioning VersionableItem for the given content
    instance or model, or None if versioning is not installed or the model
    is not registered as a versionable.
    """
    if not _versioning_installed():
        return None
    try:
        from djangocms_versioning import versionables
    except ImportError:
        return None
    try:
        return versionables.for_content(instance_or_model)
    except KeyError:
        return None


def get_default_language_sibling(content, mirror_state_from=None):
    """
    Return the sibling content object in the configured default language.

    Three branches:

    1. Versioned + language-aware content (any model whose VersionableItem
       includes ``"language"`` in ``extra_grouping_fields``). Uses
       ``VersionableItem.grouping_values`` to capture all grouping field
       values from the source instance, then overrides the language key
       with the default language and queries the model's base manager.
       When ``mirror_state_from`` is given and has a ``versions`` reverse
       relation, the lookup additionally matches Version.state.

    2. PageContent without versioning installed. Falls back to filtering by
       ``page`` + ``language`` so the addon keeps working on CMS 4 setups
       that skip djangocms-versioning.

    3. Anything else (non-versioned non-PageContent, versioned but not
       language-grouped, or no ``language`` field at all). Returns None;
       callers treat this as "leave the placeholder alone". The
       django-modeltranslation "one record per all languages" pattern
       lands here intentionally.
    """
    default_lang = get_untranslated_default_language_if_enabled()
    if not default_lang or content is None:
        return None

    versionable = get_versionable_for(content)
    if versionable is not None and 'language' in versionable.extra_grouping_fields:
        grouping = versionable.grouping_values(content)
        grouping['language'] = default_lang
        qs = type(content)._base_manager.filter(**grouping)
        if mirror_state_from is not None and hasattr(mirror_state_from, 'versions'):
            source_version = mirror_state_from.versions.first()
            if source_version is not None:
                qs = qs.filter(versions__state=source_version.state)
        return qs.first()

    from cms.models import PageContent
    if isinstance(content, PageContent):
        return PageContent._base_manager.filter(
            page=content.page, language=default_lang,
        ).first()

    return None
