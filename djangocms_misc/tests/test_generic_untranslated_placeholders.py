"""
Verification for the generalized global_untranslated_placeholder addon
(plan steps 4-10). These tests cover non-PageContent versionables, a
non-versioned modeltranslation-style model, a versioned non-language
versionable, and the PageContent-without-versioning fallback.
"""
from unittest import mock

from cms.api import add_plugin
from cms.utils.placeholder import get_placeholder_from_slot
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, TestCase, override_settings

from djangocms_misc.global_untranslated_placeholder import utils
from djangocms_misc.global_untranslated_placeholder.apps import (
    MIDDLEWARE_PATH,
    TOOLBAR_MIDDLEWARE_PATH,
    GlobalUntranslatedPlaceholderConfig,
)
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


def _make_user(username="tester"):
    return get_user_model().objects.create_superuser(
        username=username,
        email=f"{username}@example.com",
        password="pw",
    )


def _make_blogpost_content(post, language, title, user, state=None):
    """Create a BlogPostContent and its associated DRAFT Version (or the
    given state, by publishing first)."""
    from djangocms_versioning import constants as v_const
    from djangocms_versioning.models import Version

    content = BlogPostContent.objects.create(post=post, language=language, title=title)
    version = Version.objects.create(content=content, created_by=user)
    if state == v_const.PUBLISHED:
        version.publish(user)
    return content


def _make_blogpost_with_languages(en_text="en text", de_text="de text"):
    user = _make_user()
    post = BlogPost.objects.create(name="hello-post")
    en = _make_blogpost_content(post, "en", "Hello", user)
    de = _make_blogpost_content(post, "de", "Hallo", user)
    en_ph = get_placeholder_from_slot(en.placeholders, "content")
    de_ph = get_placeholder_from_slot(de.placeholders, "content")
    add_plugin(en_ph, TestPlugin, "en", field1=en_text)
    add_plugin(de_ph, TestPlugin, "de", field1=de_text)
    return post, en, de, en_ph, de_ph


class GenericSiblingResolverTests(TestCase):
    """Step 4 + part of step 7: helper-level coverage of get_default_language_sibling
    for a non-PageContent versionable."""

    def test_versionable_language_grouped_sibling_lookup(self):
        post, en, de, _, _ = _make_blogpost_with_languages()
        sibling = utils.get_default_language_sibling(de)
        self.assertIsNotNone(sibling)
        self.assertEqual(sibling.pk, en.pk)
        self.assertEqual(sibling.language, "en")

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
        post = BlogPost.objects.create(name="lonely-post")
        de = BlogPostContent.objects.create(post=post, language="de", title="Hallo")
        de_ph = get_placeholder_from_slot(de.placeholders, "content")
        swapped = _resolve_default_placeholder(de_ph)
        self.assertEqual(swapped.pk, de_ph.pk)

    def test_renderer_forces_default_language_when_caller_passes_one(self):
        """Regression: ContentRenderer.render_placeholder was previously
        wrapped with *args/**kwargs, so when a caller passed language='de'
        explicitly (e.g. `{% render_placeholder x language='de' %}`,
        cms_alias_tags, or a custom view), the swapped en placeholder was
        rendered while plugins were filtered by 'de' — yielding empty output
        in real projects, even though the regular `{% placeholder %}` tag
        worked. This asserts the patch forces language to the default."""
        from cms.plugin_rendering import ContentRenderer
        from django.template import Context
        from django.test import RequestFactory

        post, en, de, en_ph, de_ph = _make_blogpost_with_languages(
            en_text="visible-en",
            de_text="hidden-de",
        )
        request = RequestFactory().get("/de/")
        request.session = {}
        request.user = get_user_model()(is_staff=False, is_superuser=False)
        renderer = ContentRenderer(request=request)
        context = Context({"request": request})

        # Pass language='de' explicitly — the old code would propagate this
        # to the original render_placeholder, which would filter the swapped
        # en placeholder's plugins by language='de' and render nothing.
        rendered = renderer.render_placeholder(de_ph, context, language="de")

        self.assertIn("visible-en", str(rendered))
        self.assertNotIn("hidden-de", str(rendered))


class EditUrlRedirectTests(TestCase):
    """Step 6: middleware redirects edit URLs for the de BlogPostContent
    to the equivalent en BlogPostContent URL, preserving the URL prefix."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.middleware = EditModeDefaultLanguageMiddleware(get_response=lambda r: None)
        self.user = get_user_model().objects.create_superuser(
            username="editor",
            email="e@e.com",
            password="pw",
        )

    def _process(
        self, request, ct_id, obj_id, url_name="cms_placeholder_render_object_edit"
    ):
        match = mock.Mock(url_name=url_name)
        request.resolver_match = match
        request.user = self.user
        return self.middleware.process_view(
            request, None, (str(ct_id), str(obj_id)), {}
        )

    def test_de_blogpost_redirects_to_en_blogpost(self):
        post, en, de, _, _ = _make_blogpost_with_languages()
        ct_id = ContentType.objects.get_for_model(BlogPostContent).id
        path = f"/de/admin/cms/placeholder/object/{ct_id}/edit/{de.pk}/"
        request = self.factory.get(path)
        response = self._process(request, ct_id, de.pk)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 302)
        # URL prefix preserved at /de/, only the trailing object id swapped.
        self.assertEqual(
            response["Location"],
            f"/de/admin/cms/placeholder/object/{ct_id}/edit/{en.pk}/",
        )

    def test_en_blogpost_is_not_redirected(self):
        post, en, de, _, _ = _make_blogpost_with_languages()
        ct_id = ContentType.objects.get_for_model(BlogPostContent).id
        path = f"/en/admin/cms/placeholder/object/{ct_id}/edit/{en.pk}/"
        request = self.factory.get(path)
        response = self._process(request, ct_id, en.pk)
        self.assertIsNone(response)


class VersioningEditRedirectUrlPrefixTests(TestCase):
    """When ``_call_toolbar`` activates ``force_language(toolbar_language)``
    (a user-UI preference, NOT the URL prefix language), the toolbar's
    Edit / Neuer Entwurf button's ``reverse(…edit_redirect)`` call produces
    the wrong URL prefix. The middleware catches the request here and
    rewrites the leading language segment to match the language baked
    into the version's content."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.middleware = EditModeDefaultLanguageMiddleware(
            get_response=lambda r: None,
        )

    def _process(self, request, version_id, url_name):
        match = mock.Mock(url_name=url_name)
        request.resolver_match = match
        request.user = mock.Mock(is_authenticated=True, is_staff=True)
        return self.middleware.process_view(
            request,
            None,
            (str(version_id),),
            {},
        )

    def test_redirects_to_content_language_prefix(self):
        from djangocms_versioning.models import Version

        post, en, de, _, _ = _make_blogpost_with_languages()
        de_version = Version.objects.get_for_content(de)
        path = f"/en/admin/cms/blogpostcontentversion/{de_version.pk}/edit-redirect/"
        request = self.factory.post(path)
        response = self._process(
            request,
            de_version.pk,
            "cms_blogpostcontentversion_edit_redirect",
        )
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 302)
        # URL prefix rewritten from /en/ to /de/ (de_version.content.language).
        self.assertEqual(
            response["Location"],
            f"/de/admin/cms/blogpostcontentversion/{de_version.pk}/edit-redirect/",
        )

    def test_no_redirect_when_prefix_already_matches(self):
        from djangocms_versioning.models import Version

        post, en, de, _, _ = _make_blogpost_with_languages()
        de_version = Version.objects.get_for_content(de)
        path = f"/de/admin/cms/blogpostcontentversion/{de_version.pk}/edit-redirect/"
        request = self.factory.post(path)
        response = self._process(
            request,
            de_version.pk,
            "cms_blogpostcontentversion_edit_redirect",
        )
        self.assertIsNone(response)

    def test_no_redirect_for_unrelated_url_name(self):
        post, en, de, _, _ = _make_blogpost_with_languages()
        path = "/en/admin/whatever/"
        request = self.factory.get(path)
        response = self._process(request, 1, "whatever_view")
        self.assertIsNone(response)


class VersioningActionUrlPrefixTests(TestCase):
    """djangocms-versioning's version-id-keyed action endpoints
    (``*_publish`` / ``*_unpublish`` / ``*_revert`` / ``*_archive`` /
    ``*_discard``) compute their post-action redirect URL from
    ``version.content.language``. Under this addon, ``content.language``
    is always the default ('en'), so every redirect lands on ``/en/...``
    regardless of which language URL the editor was on.

    We can't 302 the inbound POST itself — these endpoints are POST-only
    and a 302 would convert POST→GET, yielding 405. We rewrite the
    response Location instead. The Referer is the source of truth — it's
    the URL the editor was on when they clicked the action button."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.middleware = EditModeDefaultLanguageMiddleware(
            get_response=lambda r: self._stub_response,
        )
        self._stub_response = None

    def _run(self, path, location, url_name, referer=None, status=302):
        from django.http import HttpResponse, HttpResponseRedirect

        if location is None:
            self._stub_response = HttpResponse(status=200)
        elif status == 302:
            self._stub_response = HttpResponseRedirect(location)
        else:
            resp = HttpResponse(status=status)
            resp["Location"] = location
            self._stub_response = resp

        request = self.factory.post(path)
        request.resolver_match = mock.Mock(url_name=url_name)
        if referer is not None:
            request.META["HTTP_REFERER"] = referer
        # The middleware calls get_response, which our setUp wired to
        # return self._stub_response.
        return self.middleware(request)

    def test_publish_response_location_rewritten_to_referer_language(self):
        """Case 1 from the plan: toolbar_language='en', editor on /de/.
        Inbound is /en/.../publish/, Referer is /de/<page>/edit/.
        Location /en/.../preview/<pk>/ is rewritten to /de/.../preview/<pk>/."""
        response = self._run(
            path="/en/admin/cms/pagecontentversion/42/publish/",
            location="/en/admin/cms/placeholder/object/13/preview/7/",
            url_name="cms_pagecontentversion_publish",
            referer="http://testserver/de/some-page/?edit",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], "/de/admin/cms/placeholder/object/13/preview/7/"
        )

    def test_publish_response_location_rewritten_when_request_matches_referer(
        self,
    ):
        """Case 2 from the plan: toolbar_language='de', editor on /de/.
        Inbound is /de/.../publish/, Referer is /de/<page>/.
        Location /en/.../preview/<pk>/ is still rewritten to /de/... because
        the rule keys off Referer→Location mismatch, not request→Location."""
        response = self._run(
            path="/de/admin/cms/pagecontentversion/42/publish/",
            location="/en/admin/cms/placeholder/object/13/preview/7/",
            url_name="cms_pagecontentversion_publish",
            referer="http://testserver/de/some-page/",
        )
        self.assertEqual(
            response["Location"], "/de/admin/cms/placeholder/object/13/preview/7/"
        )

    def test_publish_response_no_rewrite_when_intended_matches_location(self):
        response = self._run(
            path="/de/admin/cms/pagecontentversion/42/publish/",
            location="/de/admin/cms/placeholder/object/13/preview/7/",
            url_name="cms_pagecontentversion_publish",
            referer="http://testserver/de/some-page/",
        )
        self.assertEqual(
            response["Location"], "/de/admin/cms/placeholder/object/13/preview/7/"
        )

    def test_publish_response_no_rewrite_when_referer_missing(self):
        """Referer is the sole source of truth — when it's missing, no
        rewrite (the editor gets upstream's default behavior)."""
        response = self._run(
            path="/en/admin/cms/pagecontentversion/42/publish/",
            location="/en/admin/cms/placeholder/object/13/preview/7/",
            url_name="cms_pagecontentversion_publish",
            referer=None,
        )
        self.assertEqual(
            response["Location"], "/en/admin/cms/placeholder/object/13/preview/7/"
        )

    def test_publish_response_no_rewrite_when_referer_path_has_no_known_language(
        self,
    ):
        response = self._run(
            path="/en/admin/cms/pagecontentversion/42/publish/",
            location="/en/admin/cms/placeholder/object/13/preview/7/",
            url_name="cms_pagecontentversion_publish",
            referer="http://testserver/some/path/",
        )
        self.assertEqual(
            response["Location"], "/en/admin/cms/placeholder/object/13/preview/7/"
        )

    def test_publish_response_no_rewrite_when_location_has_no_language_prefix(
        self,
    ):
        response = self._run(
            path="/en/admin/cms/pagecontentversion/42/publish/",
            location="/admin/something/",
            url_name="cms_pagecontentversion_publish",
            referer="http://testserver/de/page/",
        )
        self.assertEqual(response["Location"], "/admin/something/")

    def test_publish_response_no_rewrite_for_non_3xx(self):
        response = self._run(
            path="/en/admin/cms/pagecontentversion/42/publish/",
            location="/en/admin/cms/placeholder/object/13/preview/7/",
            url_name="cms_pagecontentversion_publish",
            referer="http://testserver/de/page/",
            status=200,
        )
        # 200 response → no Location rewrite.
        self.assertEqual(
            response["Location"], "/en/admin/cms/placeholder/object/13/preview/7/"
        )
        self.assertEqual(response.status_code, 200)

    def test_publish_response_preserves_querystring_and_fragment(self):
        response = self._run(
            path="/en/admin/cms/pagecontentversion/42/publish/",
            location="/en/admin/something/?next=/en/&x=1#frag",
            url_name="cms_pagecontentversion_publish",
            referer="http://testserver/de/page/",
        )
        self.assertEqual(
            response["Location"], "/de/admin/something/?next=/en/&x=1#frag"
        )

    def test_other_action_endpoints_use_same_handler(self):
        """``_unpublish`` / ``_revert`` / ``_archive`` / ``_discard`` all
        match the suffix tuple and get the same Location rewrite."""
        for suffix in ("unpublish", "revert", "archive", "discard"):
            with self.subTest(action=suffix):
                response = self._run(
                    path=f"/en/admin/cms/pagecontentversion/42/{suffix}/",
                    location="/en/admin/cms/placeholder/object/13/preview/7/",
                    url_name=f"cms_pagecontentversion_{suffix}",
                    referer="http://testserver/de/page/",
                )
                self.assertEqual(
                    response["Location"],
                    "/de/admin/cms/placeholder/object/13/preview/7/",
                )

    def test_non_action_url_name_passes_through(self):
        response = self._run(
            path="/en/admin/cms/something/",
            location="/en/admin/elsewhere/",
            url_name="cms_something_changelist",
            referer="http://testserver/de/page/",
        )
        # No suffix match → Location untouched.
        self.assertEqual(response["Location"], "/en/admin/elsewhere/")

    @override_settings(DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS=None)
    def test_no_rewrite_when_addon_disabled(self):
        response = self._run(
            path="/en/admin/cms/pagecontentversion/42/publish/",
            location="/en/admin/cms/placeholder/object/13/preview/7/",
            url_name="cms_pagecontentversion_publish",
            referer="http://testserver/de/page/",
        )
        self.assertEqual(
            response["Location"], "/en/admin/cms/placeholder/object/13/preview/7/"
        )


class AutoCreateDefaultLanguageDraftTests(TestCase):
    """When the default-language sibling has only a PUBLISHED Version (no
    DRAFT), the middleware/helper must NOT redirect onto the immutable
    published content. Instead, mirror djangocms-versioning's "New Draft"
    button: auto-create a DRAFT via Version.copy(user) and redirect to it."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.middleware = EditModeDefaultLanguageMiddleware(get_response=lambda r: None)
        self.user = get_user_model().objects.create_superuser(
            username="editor",
            email="e@e.com",
            password="pw",
        )

    def _make_post_with_published_en_and_draft_de(self):
        """Returns (post, published_en_pc, de_pc) where en has only a
        PUBLISHED Version and de has only a DRAFT Version."""
        from djangocms_versioning import constants as v_const

        post = BlogPost.objects.create(name="auto-draft-post")
        en = _make_blogpost_content(
            post, "en", "Hello", self.user, state=v_const.PUBLISHED
        )
        de = _make_blogpost_content(post, "de", "Hallo", self.user)
        return post, en, de

    def test_helper_returns_existing_draft_without_creating_new_version(self):
        from djangocms_versioning.models import Version

        post, en, de, _, _ = _make_blogpost_with_languages()
        before = Version.objects.count()

        sibling = utils.get_default_language_editable_sibling(de, self.user)

        self.assertIsNotNone(sibling)
        self.assertEqual(sibling.pk, en.pk)
        self.assertEqual(Version.objects.count(), before)

    def test_helper_auto_creates_draft_when_only_published_exists(self):
        from django.contrib.contenttypes.models import ContentType
        from djangocms_versioning import constants as v_const
        from djangocms_versioning.models import Version

        post, published_en, de = self._make_post_with_published_en_and_draft_de()
        ct = ContentType.objects.get_for_model(BlogPostContent)
        # Use _base_manager because BlogPostContent.objects filters to
        # PUBLISHED only (djangocms-versioning's PublishedContentManagerMixin).
        en_pks_before = list(
            BlogPostContent._base_manager.filter(
                post=post,
                language="en",
            ).values_list("pk", flat=True)
        )
        en_versions_before = Version.objects.filter(
            content_type=ct,
            object_id__in=en_pks_before,
        ).count()
        self.assertEqual(en_versions_before, 1)

        sibling = utils.get_default_language_editable_sibling(de, self.user)

        # The returned sibling is a brand new DRAFT content row, not the
        # PUBLISHED one (which would be uneditable).
        self.assertIsNotNone(sibling)
        self.assertNotEqual(sibling.pk, published_en.pk)
        self.assertEqual(sibling.language, "en")
        # A new DRAFT Version was created for the en grouper.
        en_pks_after = list(
            BlogPostContent._base_manager.filter(
                post=post,
                language="en",
            ).values_list("pk", flat=True)
        )
        en_drafts_after = Version.objects.filter(
            content_type=ct,
            object_id__in=en_pks_after,
            state=v_const.DRAFT,
        )
        self.assertEqual(en_drafts_after.count(), 1)
        self.assertEqual(en_drafts_after.first().object_id, sibling.pk)

    def test_middleware_redirects_to_auto_created_draft(self):
        post, published_en, de = self._make_post_with_published_en_and_draft_de()
        ct_id = ContentType.objects.get_for_model(BlogPostContent).id
        path = f"/de/admin/cms/placeholder/object/{ct_id}/edit/{de.pk}/"
        request = self.factory.get(path)
        request.user = self.user
        match = mock.Mock(url_name="cms_placeholder_render_object_edit")
        request.resolver_match = match

        response = self.middleware.process_view(
            request,
            None,
            (str(ct_id), str(de.pk)),
            {},
        )

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 302)
        # Redirect target: same URL prefix and content type, but a new
        # object_id — neither de nor the published en.
        self.assertTrue(
            response["Location"].startswith(
                f"/de/admin/cms/placeholder/object/{ct_id}/edit/"
            )
        )
        new_pk_str = response["Location"].rstrip("/").rsplit("/", 1)[-1]
        new_pk = int(new_pk_str)
        self.assertNotEqual(new_pk, de.pk)
        self.assertNotEqual(new_pk, published_en.pk)
        # The new pk corresponds to the auto-created DRAFT. Use _base_manager
        # because the default manager filters to PUBLISHED only under
        # djangocms-versioning.
        new_content = BlogPostContent._base_manager.get(pk=new_pk)
        self.assertEqual(new_content.language, "en")

    def test_preview_url_does_not_auto_create_draft(self):
        """Preview is read-only — viewing a preview URL must NOT have the
        side effect of creating a new DRAFT Version."""
        from djangocms_versioning.models import Version

        post, published_en, de = self._make_post_with_published_en_and_draft_de()
        versions_before = Version.objects.count()
        ct_id = ContentType.objects.get_for_model(BlogPostContent).id
        path = f"/de/admin/cms/placeholder/object/{ct_id}/preview/{de.pk}/"
        request = self.factory.get(path)
        request.user = self.user
        match = mock.Mock(url_name="cms_placeholder_render_object_preview")
        request.resolver_match = match

        self.middleware.process_view(
            request,
            None,
            (str(ct_id), str(de.pk)),
            {},
        )

        self.assertEqual(Version.objects.count(), versions_before)


class CascadePublishLanguageSiblingsTests(TestCase):
    """When any language sibling is published, also publish other-language
    siblings that have BOTH a DRAFT and an existing PUBLISHED Version."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            username="publisher",
            email="p@p.com",
            password="pw",
        )
        # Inject the current user into CMS's thread-local; that's the
        # mechanism through which the cascade signal handler picks up the
        # publishing user (the same one CurrentUserMiddleware would set).
        from cms.utils.permissions import set_current_user

        set_current_user(self.user)

    def tearDown(self):
        from cms.utils.permissions import set_current_user

        set_current_user(None)
        super().tearDown()

    def _make_draft(self, post, language, title):
        from djangocms_versioning.models import Version

        content = BlogPostContent.objects.create(
            post=post,
            language=language,
            title=title,
        )
        return Version.objects.create(content=content, created_by=self.user)

    def _publish_then_redraft(self, post, language, title):
        """Create + publish a sibling, then create a new DRAFT on top.
        Returns (published_version, new_draft_version)."""
        published = self._make_draft(post, language, title)
        published.publish(self.user)
        # Create a separate draft for the same grouper+language
        new_draft = self._make_draft(post, language, title + " v2")
        return published, new_draft

    def _state_of(self, version):
        # FSM fields don't allow refresh_from_db setattr; re-fetch instead.
        from djangocms_versioning.models import Version

        return Version.objects.get(pk=version.pk).state

    def test_cascades_to_sibling_with_draft_and_published(self):
        from djangocms_versioning import constants as v_const

        post = BlogPost.objects.create(name="cascade-post")
        en_published, en_draft = self._publish_then_redraft(post, "en", "Hello")
        de_published, de_draft = self._publish_then_redraft(post, "de", "Hallo")

        en_draft.publish(self.user)

        # de DRAFT should now be PUBLISHED (cascade).
        self.assertEqual(self._state_of(de_draft), v_const.PUBLISHED)
        # The original de PUBLISHED gets UNPUBLISHED (versioning's normal
        # flow when a new version of the same grouping gets published).
        self.assertEqual(self._state_of(de_published), v_const.UNPUBLISHED)

    def test_does_not_cascade_to_sibling_with_only_draft(self):
        from djangocms_versioning import constants as v_const

        post = BlogPost.objects.create(name="no-published-post")
        en_published, en_draft = self._publish_then_redraft(post, "en", "Hello")
        # de has only a DRAFT — never published.
        de_draft = self._make_draft(post, "de", "Hallo")
        self.assertEqual(self._state_of(de_draft), v_const.DRAFT)

        en_draft.publish(self.user)

        # de DRAFT must remain DRAFT — the "existing PUBLISHED required"
        # gate prevents silently making /de/ reachable for the first time.
        self.assertEqual(self._state_of(de_draft), v_const.DRAFT)

    def test_does_not_cascade_to_sibling_with_no_draft(self):
        from djangocms_versioning import constants as v_const
        from djangocms_versioning.models import Version

        post = BlogPost.objects.create(name="no-draft-post")
        en_published, en_draft = self._publish_then_redraft(post, "en", "Hello")
        # de is published but has no pending DRAFT.
        de_published_version = self._make_draft(post, "de", "Hallo")
        de_published_version.publish(self.user)

        version_count_before = Version.objects.count()
        en_draft.publish(self.user)

        # Nothing new created for de; de PUBLISHED untouched.
        self.assertEqual(Version.objects.count(), version_count_before)
        self.assertEqual(self._state_of(de_published_version), v_const.PUBLISHED)

    def test_cascade_is_symmetric(self):
        """Publishing a non-default language also cascades."""
        from djangocms_versioning import constants as v_const

        post = BlogPost.objects.create(name="symmetric-post")
        en_published, en_draft = self._publish_then_redraft(post, "en", "Hello")
        de_published, de_draft = self._publish_then_redraft(post, "de", "Hallo")

        de_draft.publish(self.user)

        # en DRAFT should now be PUBLISHED too.
        self.assertEqual(self._state_of(en_draft), v_const.PUBLISHED)

    @override_settings(DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS=None)
    def test_no_cascade_when_addon_disabled(self):
        from djangocms_versioning import constants as v_const

        post = BlogPost.objects.create(name="disabled-post")
        en_published, en_draft = self._publish_then_redraft(post, "en", "Hello")
        de_published, de_draft = self._publish_then_redraft(post, "de", "Hallo")

        en_draft.publish(self.user)

        self.assertEqual(self._state_of(de_draft), v_const.DRAFT)

    def test_reentrance_guard_no_infinite_loop(self):
        """Three siblings each with DRAFT+PUBLISHED. Publishing one must
        cascade to the others exactly once — the re-entrance guard
        prevents the cascaded publishes from re-firing the cascade."""
        from djangocms_versioning import constants as v_const

        post = BlogPost.objects.create(name="reentrance-post")
        en_published, en_draft = self._publish_then_redraft(post, "en", "Hello")
        de_published, de_draft = self._publish_then_redraft(post, "de", "Hallo")
        fr_published, fr_draft = self._publish_then_redraft(post, "fr", "Bonjour")

        en_draft.publish(self.user)

        # de DRAFT → PUBLISHED, fr DRAFT → PUBLISHED — each exactly once.
        self.assertEqual(self._state_of(de_draft), v_const.PUBLISHED)
        self.assertEqual(self._state_of(fr_draft), v_const.PUBLISHED)
        # And each language's previously-PUBLISHED is now UNPUBLISHED.
        self.assertEqual(self._state_of(de_published), v_const.UNPUBLISHED)
        self.assertEqual(self._state_of(fr_published), v_const.UNPUBLISHED)


class PluginLanguageSignalTests(TestCase):
    """Step 7: pre_save signal pins CMSPlugin.language to the default
    language for ANY content model, not just PageContent."""

    def test_signal_pins_blogpost_plugin_language(self):
        # Fresh BlogPostContent with no pre-existing plugins, so the
        # add_plugin call below is the only one in this placeholder.
        post = BlogPost.objects.create(name="signal-post")
        de = BlogPostContent.objects.create(post=post, language="de", title="Hallo")
        de_ph = get_placeholder_from_slot(de.placeholders, "content")
        plugin = add_plugin(de_ph, TestPlugin, "de", field1="forced")
        self.assertEqual(plugin.language, "en")

    def test_signal_pins_note_plugin_language(self):
        note = Note.objects.create(title="greetings")
        note_ph = get_placeholder_from_slot(note.placeholders, "content")
        plugin = add_plugin(note_ph, TestPlugin, "de", field1="from de")
        self.assertEqual(plugin.language, "en")


class NoteModeltranslationStyleTests(TestCase):
    """Step 9: a model with no `language` field is treated as "single record
    holds all languages". The resolver leaves its placeholders alone; the
    signal pins plugin.language."""

    def test_note_resolver_returns_original_placeholder(self):
        note = Note.objects.create(title="greetings")
        note_ph = get_placeholder_from_slot(note.placeholders, "content")
        swapped = _resolve_default_placeholder(note_ph)
        self.assertEqual(swapped.pk, note_ph.pk)

    def test_note_sibling_helper_returns_none(self):
        note = Note.objects.create(title="greetings")
        self.assertIsNone(utils.get_default_language_sibling(note))

    def test_note_edit_url_not_redirected(self):
        note = Note.objects.create(title="greetings")
        ct_id = ContentType.objects.get_for_model(Note).id
        factory = RequestFactory()
        path = f"/de/admin/cms/placeholder/object/{ct_id}/edit/{note.pk}/"
        request = factory.get(path)
        request.resolver_match = mock.Mock(
            url_name="cms_placeholder_render_object_edit"
        )
        middleware = EditModeDefaultLanguageMiddleware(get_response=lambda r: None)
        response = middleware.process_view(
            request, None, (str(ct_id), str(note.pk)), {}
        )
        self.assertIsNone(response)


class RegionVersionedWithoutLanguageTests(TestCase):
    """Step 10: a versionable whose extra_grouping_fields does NOT contain
    'language' is left alone by the resolver and middleware."""

    def test_region_sibling_helper_returns_none(self):
        region = Region.objects.create(name="eu")
        content = RegionContent.objects.create(region=region, title="EU")
        self.assertIsNone(utils.get_default_language_sibling(content))

    def test_region_resolver_returns_original_placeholder(self):
        region = Region.objects.create(name="eu")
        content = RegionContent.objects.create(region=region, title="EU")
        region_ph = get_placeholder_from_slot(content.placeholders, "content")
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
            "home",
            template="base.html",
            language="en",
            created_by=get_user_model().objects.create_superuser(
                username="u",
                email="u@u.com",
                password="p",
            ),
        )
        # Fetch the en PageContent that create_page produced.
        en_pc = PageContent._base_manager.filter(page=page, language="en").first()
        self.assertIsNotNone(en_pc)
        # Manually create a de PageContent row (skipping the full versioning
        # add-translation flow, which is not what we are testing here).
        de_pc = PageContent._base_manager.create(
            page=page,
            language="de",
            title="home-de",
            template="base.html",
        )

        with mock.patch.object(utils, "_versioning_installed", return_value=False):
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


class AppReadyConfigCheckTests(TestCase):
    """`GlobalUntranslatedPlaceholderConfig.ready()` raises ImproperlyConfigured
    when the addon is enabled but the redirect middleware is missing or
    misordered. When the addon is disabled, the check is skipped."""

    def _app_config(self):
        return GlobalUntranslatedPlaceholderConfig.create(
            "djangocms_misc.global_untranslated_placeholder",
        )

    @override_settings(
        DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS="en",
        MIDDLEWARE=["cms.middleware.toolbar.ToolbarMiddleware"],
    )
    def test_raises_when_addon_enabled_and_middleware_missing(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self._app_config().ready()
        self.assertIn(MIDDLEWARE_PATH, str(ctx.exception))
        self.assertIn("not in MIDDLEWARE", str(ctx.exception))

    @override_settings(
        DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS="en",
        MIDDLEWARE=[
            MIDDLEWARE_PATH,
            TOOLBAR_MIDDLEWARE_PATH,
        ],
    )
    def test_raises_when_redirect_middleware_before_toolbar(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self._app_config().ready()
        self.assertIn("must appear AFTER", str(ctx.exception))

    @override_settings(
        DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS=None,
        MIDDLEWARE=[],
    )
    def test_does_not_raise_when_addon_disabled(self):
        # No middleware at all, no setting — should be a no-op.
        self._app_config().ready()

    @override_settings(
        DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS="en",
        MIDDLEWARE=[
            TOOLBAR_MIDDLEWARE_PATH,
            MIDDLEWARE_PATH,
        ],
    )
    def test_does_not_raise_when_properly_configured(self):
        self._app_config().ready()
