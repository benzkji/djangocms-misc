# -*- coding: utf-8 -*-
from cms.api import add_plugin, create_page, create_page_content
from cms.models import PageContent
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.test import Client
from django.test.testcases import TestCase

from djangocms_misc.tests.test_app.cms_plugins import TestPlugin


class UntranslatedPlaceholderTestCase(TestCase):
    def setUp(self):
        # The djangocms_misc.autopublisher app is not ported to CMS 4 yet;
        # when test_autopublisher.py runs @modify_settings(INSTALLED_APPS=...)
        # it connects a post_save handler that calls CMS-3.x-only methods,
        # and the handler stays connected for the rest of the test process.
        # Defensively disconnect it.
        post_save.disconnect(
            sender=None,
            dispatch_uid="cms_autopublisher_publish_check_save_plugin_instance",
        )
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@admin.com",
            password="pass",
        )

    def _get_pagecontent(self, page, language):
        return PageContent.admin_manager.filter(page=page, language=language).first()

    def _placeholder(self, page_content, slot):
        return page_content.get_placeholders().get(slot=slot)

    def _publish(self, page_content):
        page_content.versions.first().publish(self.user)

    def test_basic(self):
        """Plugins added to the default-language PageContent render in both
        languages (the renderer swap surfaces the en plugins for /de/)."""
        page = create_page("page_en", "base.html", "en", created_by=self.user)
        create_page_content("de", "page_de", page, created_by=self.user)
        en_pc = self._get_pagecontent(page, "en")
        de_pc = self._get_pagecontent(page, "de")

        placeholder_en = self._placeholder(en_pc, "untranslated_placeholder")
        add_plugin(placeholder_en, TestPlugin, "en", field1="en field1")

        self._publish(en_pc)
        self._publish(de_pc)

        content_en = self.client.get(page.get_absolute_url("en"))
        self.assertRegex(str(content_en.content), "en field1")
        content_de = self.client.get(page.get_absolute_url("de"))
        self.assertRegex(str(content_de.content), "en field1")

    def test_pre_save_signal_pins_plugin_language(self):
        """Programmatic add_plugin with language='de' produces a plugin row
        with language='en' (pre_save signal). Note this does NOT relocate
        the plugin to the en placeholder — only the HTTP edit-URL redirect
        (middleware) does that. See test_generic_untranslated_placeholders
        for the relocation flow."""
        page = create_page("page_en", "base.html", "en", created_by=self.user)
        create_page_content("de", "page_de", page, created_by=self.user)
        de_pc = self._get_pagecontent(page, "de")

        placeholder_de = self._placeholder(de_pc, "untranslated_placeholder")
        plugin = add_plugin(placeholder_de, TestPlugin, "de", field1="pinned")

        self.assertEqual(plugin.language, "en")
