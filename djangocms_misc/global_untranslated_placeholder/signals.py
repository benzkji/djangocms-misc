from django.db.models.signals import pre_save
from django.dispatch import receiver

from cms.models import CMSPlugin

from djangocms_misc.global_untranslated_placeholder.utils import (
    get_untranslated_default_language_if_enabled,
)


@receiver(
    pre_save,
    dispatch_uid="cmsplugin_pre_save_set_language",
)
def pre_cms_plugin_save(**kwargs):
    """
    Defense in depth: force every CMSPlugin to be saved in the configured
    default language. Catches programmatic writes that bypass the edit-mode
    URL redirect (e.g. cms.api.add_plugin called from a management command
    or from djangocms-versioning's cross-language copy).
    """
    instance = kwargs.get('instance', None)
    if instance and isinstance(instance, CMSPlugin):
        lang = get_untranslated_default_language_if_enabled()
        if lang:
            instance.language = lang
