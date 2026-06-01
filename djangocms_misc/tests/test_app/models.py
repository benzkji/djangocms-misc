from cms.models import CMSPlugin
from cms.models.fields import PlaceholderRelationField
from django.db import models


class TestPluginModel(CMSPlugin):
    field1 = models.CharField(max_length=64, default='', blank=False)
    field_date = models.DateField(default=None, null=True, )
    field_datetime = models.DateTimeField(default=None, null=True, )
    field_time = models.TimeField(default=None, null=True, )

    def __str__(self):
        return self.field1


class TestModel(models.Model):
    field0 = models.CharField(max_length=64, default='', blank=True)
    field1 = models.CharField(max_length=64, default='', blank=False)
    field2 = models.CharField(max_length=64, default='', blank=True)

    def __str__(self):
        return self.field1


class TestInlineModel(models.Model):
    testmodel = models.ForeignKey(TestModel, on_delete=models.CASCADE)
    field1 = models.CharField(max_length=64, default='', blank=False)
    field2 = models.CharField(max_length=64, default='', blank=True)

    def __str__(self):
        return self.field1


# --- Fixtures for global_untranslated_placeholder generic-coverage tests ---


class BlogPost(models.Model):
    """Grouper for the BlogPostContent versionable (language-grouped)."""
    name = models.CharField(max_length=255, default='')

    def __str__(self):
        return self.name


class BlogPostContent(models.Model):
    """Per-language, versionable content for a BlogPost. Used to verify the
    addon's generic sibling-swap path works for any registered VersionableItem
    with ``"language"`` in its grouping fields — not only PageContent.
    """
    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name='contents')
    language = models.CharField(max_length=15, db_index=True)
    title = models.CharField(max_length=255, default='')
    placeholders = PlaceholderRelationField()

    def __str__(self):
        return f'{self.title} ({self.language})'

    def get_template(self):
        return 'base.html'


class Note(models.Model):
    """Modeltranslation-style "single record holds all languages" model.
    No ``language`` field. Used to confirm the addon leaves it alone in
    rendering / edit-URL redirect; the pre_save signal still pins the
    language of any plugin added to its placeholders.
    """
    title = models.CharField(max_length=255, default='')
    placeholders = PlaceholderRelationField()

    def __str__(self):
        return self.title

    def get_template(self):
        return 'base.html'


class Region(models.Model):
    """Grouper for a versionable that is NOT language-grouped."""
    name = models.CharField(max_length=255, default='')

    def __str__(self):
        return self.name


class RegionContent(models.Model):
    """Versionable content grouped by region only (no language). Used to
    confirm the resolver returns None for versionables whose
    ``extra_grouping_fields`` does not include ``"language"``.
    """
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name='contents')
    title = models.CharField(max_length=255, default='')
    placeholders = PlaceholderRelationField()

    def __str__(self):
        return self.title

    def get_template(self):
        return 'base.html'
