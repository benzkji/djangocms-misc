# -*- coding: utf-8 -*
from cms.api import create_page
from cms.models import PageContent
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client, TestCase, override_settings


def _publish(page, language, user):
    pc = PageContent.admin_manager.filter(page=page, language=language).first()
    pc.versions.first().publish(user)


class AlternateToolbarTests(TestCase):
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

    @override_settings(DEBUG=True)
    def test_toolbar_renders_without_pages(self):
        """
        weird recursion bug, only when DEBUG=True and without any pages
        edge case, but annonying when setting up a new site
        """
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.client.login(username="fred", password="test")
        response = self.client.get("/en/admin/")
        self.assertEqual(response.status_code, 200)
        response = self.client.get("/en/?edit")
        self.assertEqual(response.status_code, 200)
        response = self.client.get("/en/not-existing-at-all/")
        self.assertEqual(response.status_code, 404)

    def test_toolbar_renders_has_links(self):
        """
        basic tests. check for some links and if it renders at all
        """
        self.client.login(username="fred", password="test")
        page = create_page("test", "base.html", "en", created_by=self.user)
        _publish(page, "en", self.user)

        url = page.get_absolute_url()
        response = self.client.get(url)
        self.assertRegex(str(response.content), r'"/en/admin/auth/"')
        self.assertRegex(str(response.content), r'"/en/admin/password_change/"')
