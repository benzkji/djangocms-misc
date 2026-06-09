from cms.app_base import CMSAppConfig
from cms.models import Placeholder
from django.http import HttpResponse
from djangocms_versioning.datastructures import VersionableItem

from .models import BlogPostContent, Note, RegionContent


def _copy_content(original):
    """Generic copy function for the test versionables.

    Mirrors :func:`djangocms_versioning.cms_config.copy_page_content` but
    works for any content model with a ``placeholders`` relation, so we can
    reuse it for both BlogPostContent (language-grouped) and RegionContent
    (region-only).
    """
    Model = type(original)
    fields = {
        f.name: getattr(original, f.name)
        for f in Model._meta.fields
        if f.name != Model._meta.pk.name
    }
    new_content = Model._base_manager.create(**fields)
    new_placeholders = []
    for placeholder in original.placeholders.all():
        placeholder_fields = {
            f.name: getattr(placeholder, f.name)
            for f in Placeholder._meta.fields
            if f.name not in (Placeholder._meta.pk.name, "source")
        }
        if placeholder.source:
            placeholder_fields["source"] = new_content
        new_placeholder = Placeholder.objects.create(**placeholder_fields)
        placeholder.copy_plugins(new_placeholder)
        new_placeholders.append(new_placeholder)
    new_content.placeholders.add(*new_placeholders)
    return new_content


def _render(request, content_obj):
    # Minimal renderer used only so the edit URL endpoint does not 400; the
    # tests we care about either inspect redirects (no rendering needed) or
    # call the resolver directly.
    return HttpResponse(f"<html><body>{content_obj}</body></html>")


class TestAppCMSConfig(CMSAppConfig):
    cms_enabled = True
    cms_toolbar_enabled_models = [
        (BlogPostContent, _render, "post"),
        (Note, _render),
        (RegionContent, _render, "region"),
    ]
    djangocms_versioning_enabled = True
    versioning = [
        VersionableItem(
            content_model=BlogPostContent,
            grouper_field_name="post",
            extra_grouping_fields=["language"],
            version_list_filter_lookups={
                "language": lambda: [("en", "en"), ("de", "de")]
            },
            copy_function=_copy_content,
        ),
        VersionableItem(
            content_model=RegionContent,
            grouper_field_name="region",
            copy_function=_copy_content,
        ),
    ]
