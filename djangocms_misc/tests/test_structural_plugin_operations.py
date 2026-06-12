"""
Reproduction tests for structural plugin operations (copy / cut / paste)
performed from a NON-default-language page.

The structure board JS sends ``source_language`` / ``target_language`` as
``CMS.config.request.language`` — the URL-prefix language ('de' on a /de/
page). Under the addon, every plugin is pinned to the default language
('en'), so copy requests mismatch:

- copy single plugin: ``get_object_or_404(CMSPlugin, pk=…,
  language='de')`` → **404** (the error popup the editor sees)
- copy whole placeholder: ``get_plugins_list(language='de')`` → empty →
  the clipboard is silently emptied and nothing is copied (silent data
  loss, no error shown)
- cut and paste happen to survive: ``_cut_plugin`` moves by
  ``plugin.language`` and pasted plugins get re-pinned to 'en' by the
  pre_save signal.

These tests document the CURRENT (broken) behavior. Once the middleware
answers the broken requests with a friendly "switch to the default
language" response, the copy assertions here get updated.
"""
from cms.api import add_plugin, create_page
from cms.models import CMSPlugin, PageContent, Placeholder, UserSettings
from django.contrib.auth import get_user_model
from django.test import TestCase

from djangocms_misc.tests.test_app.cms_plugins import TestPlugin

COPY_URL = "/de/admin/cms/placeholder/copy-plugins/"
MOVE_URL = "/de/admin/cms/placeholder/move-plugin/"


class StructuralOperationsOnNonDefaultLanguageTests(TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            username="structural",
            email="s@s.com",
            password="pw",
        )
        self.client.force_login(self.user)
        # The clipboard the structure board targets (CMS.config.clipboard.id);
        # normally auto-created by the toolbar on the first staff request.
        self.clipboard = Placeholder.objects.create(slot="clipboard")
        UserSettings.objects.create(
            user=self.user,
            language="en",
            clipboard=self.clipboard,
        )

        page = create_page("structural", "base.html", "en", created_by=self.user)
        self.en_content = PageContent.admin_manager.filter(
            page=page, language="en"
        ).first()
        self.en_placeholder = self.en_content.get_placeholders().get(
            slot="untranslated_placeholder"
        )
        # language pinned to 'en' by the pre_save signal regardless
        self.plugin = add_plugin(self.en_placeholder, TestPlugin, "en", field1="one")
        self.edit_path = f"/de/admin/cms/placeholder/object/{self.en_content.pk}/edit/"

    def test_copy_plugin_to_clipboard_from_de_page(self):
        """'copy' on a single plugin. The JS sends source_language =
        CMS.config.request.language = 'de'; the plugin's language is 'en'.
        CURRENT BROKEN BEHAVIOR: 404 — the editor sees an error popup."""
        response = self.client.post(
            COPY_URL,
            {
                "cms_path": self.edit_path,
                "source_language": "de",
                "source_placeholder_id": self.en_placeholder.pk,
                "source_plugin_id": self.plugin.pk,
                "target_language": "de",
                "target_placeholder_id": self.clipboard.pk,
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_copy_full_placeholder_to_clipboard_from_de_page(self):
        """'copy all' on a placeholder. ``get_plugins_list(language='de')``
        finds nothing (plugins are 'en'). CURRENT BROKEN BEHAVIOR: 200,
        but the clipboard reference ends up EMPTY — the copy silently
        copies nothing."""
        response = self.client.post(
            COPY_URL,
            {
                "cms_path": self.edit_path,
                "source_language": "de",
                "source_placeholder_id": self.en_placeholder.pk,
                # no source_plugin_id → whole placeholder
                "target_language": "de",
                "target_placeholder_id": self.clipboard.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        # The clipboard now holds a PlaceholderReference wrapper...
        reference = self.clipboard.get_plugins().get()
        self.assertEqual(reference.plugin_type, "PlaceholderPlugin")
        # ...whose inner placeholder is EMPTY — nothing was actually copied.
        inner = reference.get_bound_plugin().placeholder_ref
        self.assertEqual(inner.get_plugins().count(), 0)

    def test_cut_plugin_to_clipboard_from_de_page(self):
        """'cut' = move_plugin targeting the clipboard. Survives, because
        ``_cut_plugin`` moves by ``plugin.language``, not target_language."""
        response = self.client.post(
            MOVE_URL,
            {
                "cms_path": self.edit_path,
                "plugin_id": self.plugin.pk,
                "placeholder_id": self.clipboard.pk,
                "target_language": "de",
            },
        )
        self.assertEqual(response.status_code, 200)
        moved = CMSPlugin.objects.get(pk=self.plugin.pk)
        self.assertEqual(moved.placeholder_id, self.clipboard.pk)
        self.assertEqual(self.en_placeholder.get_plugins().count(), 0)

    def test_paste_plugin_from_clipboard_on_de_page(self):
        """'paste' = move_plugin with move_a_copy from the clipboard.
        Survives in THIS minimal case (paste at the end, nothing to
        shift): the pasted plugin is created and re-pinned to 'en' by the
        pre_save signal, keeping positions coherent."""
        clipboard_plugin = add_plugin(
            self.clipboard, TestPlugin, "en", field1="clipped"
        )
        response = self.client.post(
            MOVE_URL,
            {
                "cms_path": self.edit_path,
                "plugin_id": clipboard_plugin.pk,
                "placeholder_id": self.en_placeholder.pk,
                "target_language": "de",
                "move_a_copy": "true",
                "plugin_order[]": ["__COPY__"],
                "target_position": 2,
            },
        )
        self.assertEqual(response.status_code, 200)
        plugins = list(self.en_placeholder.get_plugins().order_by("position"))
        self.assertEqual(len(plugins), 2)
        self.assertEqual({p.language for p in plugins}, {"en"})
        self.assertEqual([p.position for p in plugins], [1, 2])

    def test_paste_between_existing_plugins_on_de_page(self):
        """Paste BETWEEN existing plugins. ``_paste_plugin`` only shifts
        existing positions when ``get_last_plugin(target_language='de')``
        finds something — it never does (plugins are 'en'), so the shift
        is skipped and the pasted plugin lands on an occupied
        (placeholder, language, position) slot.

        CURRENT BROKEN BEHAVIOR: IntegrityError → 500 for the editor."""
        from django.db.utils import IntegrityError

        add_plugin(self.en_placeholder, TestPlugin, "en", field1="two")
        add_plugin(self.en_placeholder, TestPlugin, "en", field1="three")
        clipboard_plugin = add_plugin(
            self.clipboard, TestPlugin, "en", field1="clipped"
        )
        with self.assertRaises(IntegrityError):
            self.client.post(
                MOVE_URL,
                {
                    "cms_path": self.edit_path,
                    "plugin_id": clipboard_plugin.pk,
                    "placeholder_id": self.en_placeholder.pk,
                    "target_language": "de",
                    "move_a_copy": "true",
                    "plugin_order[]": ["__COPY__"],
                    # insert between plugin 1 and 2
                    "target_position": 2,
                },
            )
