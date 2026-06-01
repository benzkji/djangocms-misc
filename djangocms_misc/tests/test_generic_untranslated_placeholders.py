"""
Verification for the generalized global_untranslated_placeholder addon
(plan steps 4-10). These tests cover non-PageContent versionables, a
non-versioned modeltranslation-style model, a versioned non-language
versionable, and the PageContent-without-versioning fallback.
"""
from unittest import mock

from cms.api import add_plugin
from cms.models import Placeholder
from cms.utils.placeholder import get_placeholder_from_slot
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase, override_settings

from djangocms_misc.global_untranslated_placeholder import utils
from djangocms_misc.global_untranslated_placeholder.middleware import (
    EditModeDefaultLanguageMiddleware,
)
from djangocms_misc.global_untranslated_placeholder.models import (
    _resolve_default_placeholder,
)
from djangocms_misc.tests.test_app.cms_plugins import TestPlugin
from djangocms_misc.tests.test_app.models import (
    BlogPost,
    BlogPostContent,
    Note,
    Region,
    RegionContent,
)


def _make_blogpost_with_languages(en_text='en text', de_text='de text'):
    post = BlogPost.objects.create(name='hello-post')
    en = BlogPostContent.objects.create(post=post, language='en', title='Hello')
    de = BlogPostContent.objects.create(post=post, language='de', title='Hallo')
    en_ph = get_placeholder_from_slot(en.placeholders, 'content')
    de_ph = get_placeholder_from_slot(de.placeholders, 'content')
    add_plugin(en_ph, TestPlugin, 'en', field1=en_text)
    add_plugin(de_ph, TestPlugin, 'de', field1=de_text)
    return post, en, de, en_ph, de_ph


class GenericSiblingResolverTests(TestCase):
    """Step 4 + part of step 7: helper-level coverage of get_default_language_sibling
    for a non-PageContent versionable."""

    def test_versionable_language_grouped_sibling_lookup(self):
        post, en, de, _, _ = _make_blogpost_with_languages()
        sibling = utils.get_default_language_sibling(de)
        self.assertIsNotNone(sibling)
        self.assertEqual(sibling.pk, en.pk)
        self.assertEqual(sibling.language, 'en')

    def test_default_language_input_returns_sibling_in_default_language(self):
        post, en, de, _, _ = _make_blogpost_with_languages()
        # When called for the en side, the lookup still matches itself; this
        # is harmless because the resolver in models.py short-circuits before
        # calling the helper when source.language == default.
        sibling = utils.get_default_language_sibling(en)
        self.assertIsNotNone(sibling)
        self.assertEqual(sibling.pk, en.pk)


class RendererPlaceholderSwapTests(TestCase):
    """Step 5: the renderer-side resolver swaps a non-default-language
    placeholder for the default-language sibling's same-slot placeholder."""

    def test_resolver_swaps_blogpost_placeholder(self):
        post, en, de, en_ph, de_ph = _make_blogpost_with_languages()
        swapped = _resolve_default_placeholder(de_ph)
        self.assertEqual(swapped.pk, en_ph.pk)

    def test_resolver_returns_original_for_default_language(self):
        post, en, de, en_ph, de_ph = _make_blogpost_with_languages()
        swapped = _resolve_default_placeholder(en_ph)
        self.assertEqual(swapped.pk, en_ph.pk)

    def test_resolver_returns_original_when_no_sibling_exists(self):
        post = BlogPost.objects.create(name='lonely-post')
        de = BlogPostContent.objects.create(post=post, language='de', title='Hallo')
        de_ph = get_placeholder_from_slot(de.placeholders, 'content')
        swapped = _resolve_default_placeholder(de_ph)
        self.assertEqual(swapped.pk, de_ph.pk)


class EditUrlRedirectTests(TestCase):
    """Step 6: middleware redirects edit URLs for the de BlogPostContent
    to the equivalent en BlogPostContent URL, preserving the URL prefix."""

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = EditModeDefaultLanguageMiddleware(get_response=lambda r: None)

    def _process(self, request, ct_id, obj_id):
        match = mock.Mock(url_name='cms_placeholder_render_object_edit')
        request.resolver_match = match
        return self.middleware.process_view(request, None, (str(ct_id), str(obj_id)), {})

    def test_de_blogpost_redirects_to_en_blogpost(self):
        post, en, de, _, _ = _make_blogpost_with_languages()
        ct_id = ContentType.objects.get_for_model(BlogPostContent).id
        path = f'/de/admin/cms/placeholder/object/{ct_id}/edit/{de.pk}/'
        request = self.factory.get(path)
        response = self._process(request, ct_id, de.pk)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 302)
        # URL prefix preserved at /de/, only the trailing object id swapped.
        self.assertEqual(
            response['Location'],
            f'/de/admin/cms/placeholder/object/{ct_id}/edit/{en.pk}/',
        )

    def test_en_blogpost_is_not_redirected(self):
        post, en, de, _, _ = _make_blogpost_with_languages()
        ct_id = ContentType.objects.get_for_model(BlogPostContent).id
        path = f'/en/admin/cms/placeholder/object/{ct_id}/edit/{en.pk}/'
        request = self.factory.get(path)
        response = self._process(request, ct_id, en.pk)
        self.assertIsNone(response)


class PluginLanguageSignalTests(TestCase):
    """Step 7: pre_save signal pins CMSPlugin.language to the default
    language for ANY content model, not just PageContent."""

    def test_signal_pins_blogpost_plugin_language(self):
        # Fresh BlogPostContent with no pre-existing plugins, so the
        # add_plugin call below is the only one in this placeholder.
        post = BlogPost.objects.create(name='signal-post')
        de = BlogPostContent.objects.create(post=post, language='de', title='Hallo')
        de_ph = get_placeholder_from_slot(de.placeholders, 'content')
        plugin = add_plugin(de_ph, TestPlugin, 'de', field1='forced')
        self.assertEqual(plugin.language, 'en')

    def test_signal_pins_note_plugin_language(self):
        note = Note.objects.create(title='greetings')
        note_ph = get_placeholder_from_slot(note.placeholders, 'content')
        plugin = add_plugin(note_ph, TestPlugin, 'de', field1='from de')
        self.assertEqual(plugin.language, 'en')


class NoteModeltranslationStyleTests(TestCase):
    """Step 9: a model with no `language` field is treated as "single record
    holds all languages". The resolver leaves its placeholders alone; the
    signal pins plugin.language."""

    def test_note_resolver_returns_original_placeholder(self):
        note = Note.objects.create(title='greetings')
        note_ph = get_placeholder_from_slot(note.placeholders, 'content')
        swapped = _resolve_default_placeholder(note_ph)
        self.assertEqual(swapped.pk, note_ph.pk)

    def test_note_sibling_helper_returns_none(self):
        note = Note.objects.create(title='greetings')
        self.assertIsNone(utils.get_default_language_sibling(note))

    def test_note_edit_url_not_redirected(self):
        note = Note.objects.create(title='greetings')
        ct_id = ContentType.objects.get_for_model(Note).id
        factory = RequestFactory()
        path = f'/de/admin/cms/placeholder/object/{ct_id}/edit/{note.pk}/'
        request = factory.get(path)
        request.resolver_match = mock.Mock(url_name='cms_placeholder_render_object_edit')
        middleware = EditModeDefaultLanguageMiddleware(get_response=lambda r: None)
        response = middleware.process_view(request, None, (str(ct_id), str(note.pk)), {})
        self.assertIsNone(response)


class RegionVersionedWithoutLanguageTests(TestCase):
    """Step 10: a versionable whose extra_grouping_fields does NOT contain
    'language' is left alone by the resolver and middleware."""

    def test_region_sibling_helper_returns_none(self):
        region = Region.objects.create(name='eu')
        content = RegionContent.objects.create(region=region, title='EU')
        self.assertIsNone(utils.get_default_language_sibling(content))

    def test_region_resolver_returns_original_placeholder(self):
        region = Region.objects.create(name='eu')
        content = RegionContent.objects.create(region=region, title='EU')
        region_ph = get_placeholder_from_slot(content.placeholders, 'content')
        swapped = _resolve_default_placeholder(region_ph)
        self.assertEqual(swapped.pk, region_ph.pk)


class PageContentNoVersioningFallbackTests(TestCase):
    """Step 8: when djangocms-versioning is not installed, the helper falls
    back to a page+language filter on PageContent.

    We simulate "no versioning" by patching the addon's internal
    `_versioning_installed` check to return False.
    """

    def test_fallback_filters_pagecontent_by_page_and_language(self):
        from cms.api import create_page
        from cms.models import PageContent

        # CMS 4 + djangocms-versioning normally creates one PageContent per
        # create_page call (draft). For this fallback test we just need two
        # PageContent rows for the same Page, one per language.
        page = create_page(
            'home',
            template='base.html',
            language='en',
            created_by=get_user_model().objects.create_superuser(
                username='u', email='u@u.com', password='p',
            ),
        )
        # Fetch the en PageContent that create_page produced.
        en_pc = PageContent._base_manager.filter(page=page, language='en').first()
        self.assertIsNotNone(en_pc)
        # Manually create a de PageContent row (skipping the full versioning
        # add-translation flow, which is not what we are testing here).
        de_pc = PageContent._base_manager.create(
            page=page, language='de', title='home-de', template='base.html',
        )

        with mock.patch.object(utils, '_versioning_installed', return_value=False):
            sibling = utils.get_default_language_sibling(de_pc)
        self.assertIsNotNone(sibling)
        self.assertEqual(sibling.pk, en_pc.pk)


@override_settings(DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS=None)
class AddonDisabledTests(TestCase):
    """Sanity check: when the addon is disabled, the resolver leaves
    everything alone for any content model."""

    def test_blogpost_not_swapped_when_addon_disabled(self):
        post, en, de, en_ph, de_ph = _make_blogpost_with_languages()
        self.assertEqual(_resolve_default_placeholder(de_ph).pk, de_ph.pk)
        self.assertIsNone(utils.get_default_language_sibling(de))
