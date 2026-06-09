import threading

from cms.models import CMSPlugin
from django.apps import apps
from django.db.models.signals import pre_save
from django.dispatch import receiver

from djangocms_misc.global_untranslated_placeholder.utils import (
    get_untranslated_default_language_if_enabled,
    iter_cascade_targets,
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
    instance = kwargs.get("instance", None)
    if instance and isinstance(instance, CMSPlugin):
        lang = get_untranslated_default_language_if_enabled()
        if lang:
            instance.language = lang


# Re-entrance guard for cascade_publish_language_siblings. When we publish a
# sibling DRAFT, versioning emits post_version_operation again; that re-entrant
# call must be a no-op so we don't iterate sibling-of-siblings recursively.
_cascade_state = threading.local()


def _connect_cascade_publish_receiver():
    """Wire up the post_version_operation receiver only when djangocms-
    versioning is installed. Done lazily to keep the addon importable in
    setups that skip versioning."""
    if not apps.is_installed("djangocms_versioning"):
        return

    from djangocms_versioning import constants as versioning_constants
    from djangocms_versioning.signals import post_version_operation

    @receiver(
        post_version_operation,
        dispatch_uid="gup_cascade_publish_language_siblings",
    )
    def cascade_publish_language_siblings(sender, operation, obj, **kwargs):
        """
        When any language sibling is published, also publish every
        other-language sibling that has BOTH a DRAFT and an existing
        PUBLISHED Version (the "existing PUBLISHED required" gate prevents
        silently making a brand-new language reachable).
        """
        if operation != versioning_constants.OPERATION_PUBLISH:
            return
        if getattr(_cascade_state, "active", False):
            return

        from cms.utils.permissions import get_current_user

        user = get_current_user()
        if user is None:
            return

        # ConditionFailed lives under djangocms_versioning.conditions; importing
        # lazily keeps the addon importable without versioning.
        try:
            from djangocms_versioning.exceptions import ConditionFailed
        except ImportError:
            ConditionFailed = Exception  # pragma: no cover

        _cascade_state.active = True
        try:
            for _language, draft_version in iter_cascade_targets(obj.content):
                try:
                    draft_version.publish(user)
                except ConditionFailed:
                    continue
                except Exception:
                    # Defensive: never let a sibling failure break the
                    # original publish operation.
                    continue
        finally:
            _cascade_state.active = False


_connect_cascade_publish_receiver()
