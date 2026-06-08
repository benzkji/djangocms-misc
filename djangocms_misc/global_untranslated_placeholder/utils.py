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


def get_default_language_editable_sibling(content, user):
    """
    Return a default-language sibling content object that is safe for the
    given user to edit.

    Unlike :func:`get_default_language_sibling`, this never returns a
    PUBLISHED versioned object. If the only default-language version is
    PUBLISHED, a brand new DRAFT is created via ``Version.copy(user)`` —
    the same code path the CMS toolbar's "New Draft" button uses — and the
    new DRAFT's content is returned.

    Used by the edit/structure-mode redirect middleware. Returns None when:
      - the addon is disabled,
      - no default-language sibling exists,
      - the user lacks permission to create a draft, or the existing draft
        is locked by another user.
    """
    default_lang = get_untranslated_default_language_if_enabled()
    if not default_lang or content is None:
        return None

    versionable = get_versionable_for(content)
    if versionable is not None and 'language' in versionable.extra_grouping_fields:
        from djangocms_versioning import constants as versioning_constants
        from djangocms_versioning.models import Version

        grouping = versionable.grouping_values(content)
        grouping['language'] = default_lang
        base_qs = type(content)._base_manager.filter(**grouping)

        # Prefer an existing DRAFT default-language sibling.
        draft = base_qs.filter(versions__state=versioning_constants.DRAFT).first()
        if draft is not None:
            return draft

        # Otherwise find the PUBLISHED default-language sibling and copy it
        # to a new DRAFT.
        published = base_qs.filter(versions__state=versioning_constants.PUBLISHED).first()
        if published is None:
            return None
        try:
            published_version = Version.objects.get_for_content(published)
        except Version.DoesNotExist:
            return None
        try:
            published_version.check_edit_redirect(user)
        except Exception:
            # ConditionFailed: locked by another user, missing permission,
            # or anon user. Caller will fall through to no-redirect.
            return None
        new_draft_version = published_version.copy(user)
        return new_draft_version.content

    from cms.models import PageContent
    if isinstance(content, PageContent):
        return PageContent._base_manager.filter(
            page=content.page, language=default_lang,
        ).first()

    return None


def iter_cascade_targets(content):
    """
    Yield ``(sibling_language, draft_version)`` tuples for every
    other-language sibling of ``content`` that has BOTH a DRAFT
    Version and an existing PUBLISHED Version for the same grouper.

    Yields nothing when:
      - the addon is disabled,
      - versioning is not installed or the content model is not registered
        as a versionable,
      - the versionable's ``extra_grouping_fields`` does not include
        ``"language"``.

    The "existing PUBLISHED required" gate ensures the cascade never
    silently makes a brand-new language reachable — it only keeps already-
    -published languages in sync.
    """
    if not get_untranslated_default_language_if_enabled() or content is None:
        return

    versionable = get_versionable_for(content)
    if versionable is None or 'language' not in versionable.extra_grouping_fields:
        return

    from django.contrib.contenttypes.models import ContentType
    from djangocms_versioning import constants as versioning_constants
    from djangocms_versioning.models import Version

    grouping = versionable.grouping_values(content)
    own_language = grouping.pop('language')
    grouper_qs = versionable.for_grouping_values(**grouping)

    other_languages = (
        grouper_qs.exclude(language=own_language)
        .values_list('language', flat=True)
        .distinct()
    )
    content_type = ContentType.objects.get_for_model(type(content))

    for sibling_language in other_languages:
        sibling_pks = list(
            grouper_qs.filter(language=sibling_language).values_list('pk', flat=True)
        )
        if not sibling_pks:
            continue
        has_published = Version.objects.filter(
            content_type=content_type,
            object_id__in=sibling_pks,
            state=versioning_constants.PUBLISHED,
        ).exists()
        if not has_published:
            continue
        draft = Version.objects.filter(
            content_type=content_type,
            object_id__in=sibling_pks,
            state=versioning_constants.DRAFT,
        ).first()
        if draft is None:
            continue
        yield sibling_language, draft
