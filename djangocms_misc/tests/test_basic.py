# -*- coding: utf-8 -*-

from cms.api import add_plugin, create_page, create_page_content
from cms.models import PageContent
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client, TestCase, modify_settings

from djangocms_misc.basic.templatetags.djangocms_misc_tags import (
    djangocms_misc_placeholder_empty,
)
from djangocms_misc.tests.test_app.cms_plugins import TestPlugin


def _get_pagecontent(page, language):
    return PageContent.admin_manager.filter(page=page, language=language).first()


def _publish(page_content, user):
    page_content.versions.first().publish(user)


class BasicAppTests(TestCase):
    def setUp(self):
        # See test_untranslated_placeholders.py for context on the defensive
        # disconnect of the autopublisher signal.
        post_save.disconnect(
            sender=None,
            dispatch_uid='cms_autopublisher_publish_check_save_plugin_instance',
        )
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='fred',
            password='test',
            email='test@test.fred',
        )

    def tearDown(self):
        pass

    def _placeholder(self, page, slot, language='en'):
        page_content = _get_pagecontent(page, language)
        return page_content.get_placeholders().get(slot=slot)

    def test_page_link_tag(self):
        page_test = create_page('test', 'base.html', 'en', created_by=self.user)
        _publish(_get_pagecontent(page_test, 'en'), self.user)
        page_home = create_page('home', 'base.html', 'en', created_by=self.user)
        page_home.reverse_id = 'home'
        page_home.save()
        _publish(_get_pagecontent(page_home, 'en'), self.user)
        response = self.client.get(page_test.get_absolute_url('en'))
        self.assertContains(response, '/en/home/')
        self.assertContains(response, '/home/">link text HOME')
        self.assertContains(response, 'class="button" href="/en/home/">')

    def test_placeholder_empty_tag(self):
        page = create_page('page_en', 'base.html', 'en', created_by=self.user)
        page.reverse_id = 'test'
        page.save()
        create_page_content('de', 'page_de', page, created_by=self.user)
        placeholder_en = self._placeholder(page, 'untranslated_placeholder', 'en')
        self.assertEqual(djangocms_misc_placeholder_empty(placeholder_en), True)
        self.assertEqual(djangocms_misc_placeholder_empty(page, 'untranslated_placeholder'), True)
        plugin = add_plugin(placeholder_en, TestPlugin, 'en')
        plugin.field1_en = 'en field1'
        plugin.save()
        self.assertEqual(djangocms_misc_placeholder_empty(placeholder_en), False)
        self.assertEqual(djangocms_misc_placeholder_empty(page, 'untranslated_placeholder'), False)

    def test_get_from_page_content_tag(self):
        """Tests if content is fetched."""
        page = create_page('page_en', 'base.html', 'en', created_by=self.user)
        page.reverse_id = 'test'
        page.save()
        create_page_content('de', 'page_de', page, created_by=self.user)
        placeholder_en = self._placeholder(page, 'untranslated_placeholder', 'en')
        plugin = add_plugin(placeholder_en, TestPlugin, 'en')
        plugin.field1_en = 'en field1'
        plugin.save()
        _publish(_get_pagecontent(page, 'en'), self.user)
        _publish(_get_pagecontent(page, 'de'), self.user)
        # untranslated placeholder is enabled, so the content should appear
        # on both /en/ and /de/ via the global_untranslated_placeholder
        # renderer swap. base.html's `djangocms_misc_get_from_page_content`
        # tag invocations are currently commented out, so we only see the
        # placeholder rendering itself.
        response = self.client.get(page.get_absolute_url('en'))
        self.assertContains(response, 'en field1', 1)
        response = self.client.get(page.get_absolute_url('de'))
        self.assertContains(response, 'en field1', 1)

    def test_language_tabs_admin_mixin(self):
        # TODO: language tabs tests
        pass

    def test_redirect_first_subpage_middleware(self):
        page_home = create_page('home', 'base.html', 'en', created_by=self.user)
        _publish(_get_pagecontent(page_home, 'en'), self.user)
        page_parent = create_page(
            'parent', 'base.html', 'en',
            redirect='/firstchild', created_by=self.user,
        )
        _publish(_get_pagecontent(page_parent, 'en'), self.user)
        page_child1 = create_page(
            'child1', 'base.html', 'en', parent=page_parent, created_by=self.user,
        )
        _publish(_get_pagecontent(page_child1, 'en'), self.user)
        page_child2 = create_page(
            'child2', 'base.html', 'en', parent=page_parent, created_by=self.user,
        )
        _publish(_get_pagecontent(page_child2, 'en'), self.user)
        # get the parent
        response = self.client.get(page_parent.get_absolute_url('en'))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, '/en/parent/child1/')
        # check for some false positives
        response = self.client.get(page_child1.get_absolute_url('en'))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(page_home.get_absolute_url('en'))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(page_child2.get_absolute_url('en'))
        self.assertEqual(response.status_code, 200)
        # 404 still ok?
        response = self.client.get('/en/absolutely-not-exising/22/')
        self.assertEqual(response.status_code, 404)

    @modify_settings(MIDDLEWARE={
        'append': 'djangocms_misc.basic.middleware.PasswordProtectedMiddleware',
    })
    def test_password_protected_middleware(self):
        page_home = create_page('home', 'base.html', 'en', created_by=self.user)
        page_home.reverse_id = 'home'
        page_home.save()
        _publish(_get_pagecontent(page_home, 'en'), self.user)
        response = self.client.get(page_home.get_absolute_url('en'))
        self.assertEqual(response.status_code, 302)

    def test_bot404_middleware(self):
        # TODO: tests
        pass
