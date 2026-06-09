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
            dispatch_uid="cms_autopublisher_publish_check_save_plugin_instance",
        )
        self.client = Client()
        self.user = User.objects.create_superuser(
            username="fred",
            password="test",
            email="test@test.fred",
        )

    def tearDown(self):
        pass

    def _placeholder(self, page, slot, language="en"):
        page_content = _get_pagecontent(page, language)
        return page_content.get_placeholders().get(slot=slot)

    def test_page_link_tag(self):
        page_test = create_page("test", "base.html", "en", created_by=self.user)
        _publish(_get_pagecontent(page_test, "en"), self.user)
        page_home = create_page("home", "base.html", "en", created_by=self.user)
        page_home.reverse_id = "home"
        page_home.save()
        _publish(_get_pagecontent(page_home, "en"), self.user)
        response = self.client.get(page_test.get_absolute_url("en"))
        self.assertContains(response, "/en/home/")
        self.assertContains(response, '/home/">link text HOME')
        self.assertContains(response, 'class="button" href="/en/home/">')

    def test_placeholder_empty_tag(self):
        page = create_page("page_en", "base.html", "en", created_by=self.user)
        page.reverse_id = "test"
        page.save()
        create_page_content("de", "page_de", page, created_by=self.user)
        placeholder_en = self._placeholder(page, "untranslated_placeholder", "en")
        self.assertEqual(djangocms_misc_placeholder_empty(placeholder_en), True)
        self.assertEqual(
            djangocms_misc_placeholder_empty(page, "untranslated_placeholder"), True
        )
        plugin = add_plugin(placeholder_en, TestPlugin, "en")
        plugin.field1_en = "en field1"
        plugin.save()
        self.assertEqual(djangocms_misc_placeholder_empty(placeholder_en), False)
        self.assertEqual(
            djangocms_misc_placeholder_empty(page, "untranslated_placeholder"), False
        )

    def test_get_from_page_content_tag(self):
        """Tests if content is fetched."""
        page = create_page("page_en", "base.html", "en", created_by=self.user)
        page.reverse_id = "test"
        page.save()
        create_page_content("de", "page_de", page, created_by=self.user)
        placeholder_en = self._placeholder(page, "untranslated_placeholder", "en")
        plugin = add_plugin(placeholder_en, TestPlugin, "en")
        plugin.field1_en = "en field1"
        plugin.save()
        _publish(_get_pagecontent(page, "en"), self.user)
        _publish(_get_pagecontent(page, "de"), self.user)
        # untranslated placeholder is enabled, so the content should appear
        # on both /en/ and /de/ via the global_untranslated_placeholder
        # renderer swap. base.html's `djangocms_misc_get_from_page_content`
        # tag invocations are currently commented out, so we only see the
        # placeholder rendering itself.
        response = self.client.get(page.get_absolute_url("en"))
        self.assertContains(response, "en field1", 1)
        response = self.client.get(page.get_absolute_url("de"))
        self.assertContains(response, "en field1", 1)

    def test_language_tabs_admin_mixin(self):
        # TODO: language tabs tests
        pass

    def test_redirect_first_subpage_middleware(self):
        page_home = create_page("home", "base.html", "en", created_by=self.user)
        _publish(_get_pagecontent(page_home, "en"), self.user)
        page_parent = create_page(
            "parent",
            "base.html",
            "en",
            redirect="/firstchild",
            created_by=self.user,
        )
        _publish(_get_pagecontent(page_parent, "en"), self.user)
        page_child1 = create_page(
            "child1",
            "base.html",
            "en",
            parent=page_parent,
            created_by=self.user,
        )
        _publish(_get_pagecontent(page_child1, "en"), self.user)
        page_child2 = create_page(
            "child2",
            "base.html",
            "en",
            parent=page_parent,
            created_by=self.user,
        )
        _publish(_get_pagecontent(page_child2, "en"), self.user)
        # get the parent
        response = self.client.get(page_parent.get_absolute_url("en"))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, "/en/parent/child1/")
        # check for some false positives
        response = self.client.get(page_child1.get_absolute_url("en"))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(page_home.get_absolute_url("en"))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(page_child2.get_absolute_url("en"))
        self.assertEqual(response.status_code, 200)
        # 404 still ok?
        response = self.client.get("/en/absolutely-not-exising/22/")
        self.assertEqual(response.status_code, 404)

    @modify_settings(
        MIDDLEWARE={
            "append": "djangocms_misc.basic.middleware.PasswordProtectedMiddleware",
        }
    )
    def test_password_protected_middleware(self):
        page_home = create_page("home", "base.html", "en", created_by=self.user)
        page_home.reverse_id = "home"
        page_home.save()
        _publish(_get_pagecontent(page_home, "en"), self.user)
        response = self.client.get(page_home.get_absolute_url("en"))
        self.assertEqual(response.status_code, 302)

    def test_bot404_middleware(self):
        # TODO: tests
        pass

    def test_render_page_placeholder_drops_poisoned_cache(self):
        """Regression: cms 4.1.11's render_page_placeholder preloads plugins
        with `get_language()` (the request language). When the request is
        on `/de/...` the preload caches an empty plugin list on the
        default-language placeholder under language='de' before the
        renderer swap and language override run, so the original
        render_placeholder returns the cached empty list and the page
        renders 0 plugins. The cache drop in patched_render_placeholder
        fixes that.

        Verified in a real project (Guardaval) with 148+ translated text
        plugins going from 0 -> 148 rendered after the fix.

        This test exercises render_page_placeholder directly because it
        was originally written when the misc test env still had an
        older djangocms-versioning whose VersionContentRenderer.
        render_obj_placeholder bypassed render_page_placeholder. That
        skip-override has since been removed in djangocms-versioning
        2.2.x+ for cms != 4.1.0/4.1.1 (gated by
        `if cms_version in ("4.1.0", "4.1.1")` in
        djangocms_versioning/plugin_rendering.py) -- on cms 4.1.11 the
        canonical {% placeholder %} path now hits
        render_page_placeholder directly, which the e2e
        test_untranslated_placeholder_visible_in_edit_mode also covers.
        We keep this direct-call test because it's deterministic and
        version-robust regardless of versioning-side gating."""
        from cms.plugin_rendering import ContentRenderer
        from django.template import Context
        from django.test import RequestFactory
        from django.utils.translation import activate

        page = create_page("page_en", "base.html", "en", created_by=self.user)
        create_page_content("de", "page_de", page, created_by=self.user)
        en_pc = _get_pagecontent(page, "en")
        de_pc = _get_pagecontent(page, "de")
        placeholder_en = en_pc.get_placeholders().get(slot="untranslated_placeholder")
        plugin = add_plugin(placeholder_en, TestPlugin, "en")
        plugin.field1_en = "en field1"
        plugin.save()
        _publish(en_pc, self.user)
        _publish(de_pc, self.user)

        # Set up a /de/ request so get_language() returns 'de' inside
        # render_page_placeholder's preload.
        factory = RequestFactory()
        request = factory.get("/de/page_de/")
        request.user = self.user
        request.current_page = page
        request.session = {}
        activate("de")

        renderer = ContentRenderer(request=request)
        context = Context({"request": request})

        result = renderer.render_page_placeholder(
            "untranslated_placeholder",
            context,
            inherit=False,
            page=page,
            editable=False,
        )

        # Without the cache-drop fix this is '' (empty).
        self.assertIn("en field1", result)

    def test_untranslated_placeholder_visible_in_edit_mode(self):
        """Editing a NON-default-language page must show the default-language
        plugins (via the edit-URL redirect to the default sibling).

        On cms 4.1.11 + djangocms-versioning >=2.2 this test ALSO
        exercises the preload-poisoning bug: the canonical
        {% placeholder %} path goes through render_page_placeholder,
        which preloads plugins under get_language() ('de' for this
        request) and caches `_plugins_cache = []` on the en placeholder
        before our patched render_placeholder runs. Without the cache
        drop in patched_render_placeholder this test fails (response
        does not contain 'en field1'); with the drop it passes."""
        from cms.toolbar.utils import get_object_edit_url

        page = create_page("page_en", "base.html", "en", created_by=self.user)
        create_page_content("de", "page_de", page, created_by=self.user)

        placeholder_en = self._placeholder(page, "untranslated_placeholder", "en")
        plugin = add_plugin(placeholder_en, TestPlugin, "en")
        plugin.field1_en = "en field1"
        plugin.save()

        # Edit the DE (non-default) content: the middleware redirects to
        # the EN default sibling, which is rendered under the /de/ language
        # prefix.
        de_pc = _get_pagecontent(page, "de")
        en_pc = _get_pagecontent(page, "en")
        self.client.force_login(self.user)
        response = self.client.get(get_object_edit_url(de_pc), follow=True)

        self.assertEqual(response.status_code, 200)
        # middleware actually redirected DE -> EN sibling
        self.assertTrue(
            any(f"/edit/{en_pc.pk}/" in url for url, _ in response.redirect_chain),
            response.redirect_chain,
        )
        # and the default-language plugin is rendered (fails without the
        # _plugins_cache drop in patched_render_placeholder)
        self.assertContains(response, "en field1")
