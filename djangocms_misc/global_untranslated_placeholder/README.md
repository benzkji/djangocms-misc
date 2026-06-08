# Global Untranslated Placeholder

Make every CMS placeholder behave as if there is only **one** language: all
plugins are stored under a single configured "default" language, and rendered
from there regardless of the request language. Per-plugin translation is then
expected to happen inside each plugin (e.g. via translated model fields).

Works with **django-cms 4.1+** (with or without `djangocms-versioning`).

---

## Quick start

1. Add the app to `INSTALLED_APPS`:

   ```python
   INSTALLED_APPS = [
       ...
       'djangocms_misc.global_untranslated_placeholder',
       ...
   ]
   ```

2. Enable the addon and set the source-of-truth language:

   ```python
   DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS = 'en'
   # Or `= True` to fall back to settings.LANGUAGE_CODE.
   # Off when unset, False, or None.
   ```

3. Add the redirect middleware **after** `cms.middleware.toolbar.ToolbarMiddleware`:

   ```python
   MIDDLEWARE = [
       ...
       'cms.middleware.toolbar.ToolbarMiddleware',
       'djangocms_misc.global_untranslated_placeholder.middleware.EditModeDefaultLanguageMiddleware',
       ...
   ]
   ```

   If the addon is enabled but the middleware is missing or misordered, the
   app's `ready()` check raises `ImproperlyConfigured` at Django startup
   with an explanatory message.

---

## What it does

When the addon is enabled, every placeholder on every language behaves like
it's pointed at the **default-language content**. Concretely:

| Aspect | Behavior |
|---|---|
| Rendering on `/de/<page>/` | Plugins from the en `PageContent` render. The renderer swaps in the default-language placeholder for each requested slot. |
| Editing on `/de/<page>/?edit` | URL is rewritten to the en object id; the editor edits the en placeholders directly. |
| Edit URL when only en PUBLISHED exists | A new en DRAFT is auto-created via `Version.copy(user)` (same code path as the toolbar's "New Draft" button), and the editor lands on the new draft. |
| Programmatic `cms.api.add_plugin(de_placeholder, ..., language='de')` | The plugin's `language` is force-pinned to `en` by a `pre_save` signal (defense in depth). |
| Plugin save reload | The structure board's post-save reload of the page (via `cms_edit_url` in the toolbar context) stays on the editor's chosen URL prefix (`/de/`) — does not flip the admin to `/en/`. |
| Publish redirect | After a successful publish from `/de/`, djangocms-versioning's `publish_view` redirects to the preview URL of the now-published content. Without the patch, that URL is built from `content_obj.language='en'` and the editor lands on `/en/.../preview/<en_pk>/`. With the patch, the redirect target keeps the editor's `/de/` prefix. |
| New-draft redirect | After "Neuer Entwurf"/New Draft on `/de/`, versioning's `edit_redirect_view` creates the en draft and redirects to its edit URL. Without the patch, `get_editable_url` derives `/en/` from `content_obj.language`. With the patch, the URL keeps the `/de/` prefix. |
| Publishing the en content | Every other-language `PageContent` for the same page that has BOTH a DRAFT and an existing PUBLISHED Version is also published. New-but-unpublished languages are left alone. |

The addon generalizes beyond `PageContent` to **any** content model registered
as a `djangocms_versioning` versionable whose `extra_grouping_fields` includes
`"language"` — for example, a custom `BlogPostContent` with grouper
`grouper_field_name="post"`, `extra_grouping_fields=["language"]`. No
per-model configuration needed; the helpers read everything from the
`VersionableItem` registry.

---

## Architecture

Four pieces, each in its own file:

```
global_untranslated_placeholder/
├── __init__.py
├── apps.py        — AppConfig with startup ImproperlyConfigured check
├── conf.py        — django-appconf settings prefix
├── middleware.py  — edit-mode URL redirect + auto-create-draft
├── models.py      — monkey-patches ContentRenderer / StructureRenderer
├── signals.py     — pre_save plugin-language pin + cascade-publish receiver
└── utils.py       — sibling resolution, gate-checking helpers
```

### 1. `models.py` — renderer monkey-patches

Patches `cms.plugin_rendering.ContentRenderer` and `StructureRenderer` so:

- `__init__(request)` sets `self.request_language = DEFAULT_LANGUAGE` whenever
  the addon is enabled.
- `render_placeholder(placeholder, context, language=…, ...)` is wrapped with
  the **real signature** (not `*args/**kwargs`) so that `language` is
  explicitly forced to the default, even when a caller passes it positionally
  or by keyword (e.g. `{% render_placeholder x language='de' %}`,
  `cms_alias_tags`, apphook views).
- After the language is forced, `placeholder._plugins_cache` and
  `placeholder._all_plugins_cache` are dropped if present. Required for
  cms 4.1.11+, where `render_page_placeholder` preloads plugins with
  `get_language()` (the request language) and caches an empty plugin list
  on the default-language placeholder before the renderer swap runs — the
  language override alone can't un-poison the cache.

Resolution: `_resolve_default_placeholder(placeholder)` reads
`placeholder.source` (the content object via `GenericForeignKey`), checks for
a `language` field, and looks up the default-language sibling. On match,
returns the sibling's same-slot `Placeholder`; on miss, returns the original.

Also in `models.py`: `_patch_toolbar_url_helpers()` wraps
`cms.toolbar.utils.get_object_{edit,preview,structure}_url`. The original
helpers have the rule `language = getattr(obj, "language", language)
# Object trumps parameter`, so even when `CMSToolbar` passes
`language=self.request_language`, the URL is reversed under
`force_language(obj.language)` and the prefix becomes `/<obj.language>/`.
With the addon enabled, an editor on `/de/` editing the default-language
sibling (an en PageContent) would see the structure board post-save
reload (via `cms_edit_url` in the toolbar context) land on `/en/`,
flipping the entire admin into English mid-edit. The patch calls the
original to keep all other concerns (live-url querystring, language-
list validation) and then post-processes the returned URL, replacing
the leading `/<obj.language>/` segment with `/<language>/` when the
caller asked for a specific language that differs from `obj.language`.
Gated on the addon being enabled, so non-untranslated projects are
unaffected.

Because `cms.toolbar.toolbar` does `from cms.toolbar.utils import
get_object_edit_url, ...` at module load (binding the originals locally),
`apps.py` connects a one-shot `request_started` signal handler that
rebinds those module-level names to the patched versions on the first
request. The rebind is deferred to first-request because
`cms.toolbar.toolbar`'s import-time code reads
`apps.get_app_config('cms').cms_extension.toolbar_mixins`, which isn't
populated until `cms.ready()` runs — and the addon's `ready()` runs
first when our app is listed before `cms` in `INSTALLED_APPS`.

`_patch_versioning_url_helpers()` (only active when
`djangocms-versioning` is installed) covers two post-redirect paths:

- **`get_preview_url`** — called by `publish_view` after publishing,
  and similar flows. The helper, when no explicit `language` is given,
  falls back to `getattr(content_obj, "language", get_language())` — so
  under our addon, where `content_obj` is always the default-language
  sibling, it always picks `'en'` and propagates to the CMS-side helper
  as a matching `language=='en'` (which skips the toolbar-URL rewrite
  above). The patch flips the default: when no `language` is passed
  AND the addon is enabled, use `django.utils.translation.get_language()`
  (the request language activated by `LocaleMiddleware`). The CMS-side
  patch then rewrites the URL's prefix from `/en/` to `/de/`.

- **`get_editable_url`** — called by `edit_redirect_view` after
  creating a new draft (e.g. on "Neuer Entwurf"). The original has no
  `language` parameter at all — it always uses
  `getattr(content_obj, "language", None)` and calls into
  `cms.toolbar.utils.get_object_edit_url`, again with a matching
  `language` so the CMS-side rewrite is skipped. The patch detects the
  same "addon enabled + request language differs from
  `content_obj.language`" condition and calls `get_object_edit_url`
  directly with the request language, letting the CMS-side rewrite
  preserve the editor's URL prefix.

Both `djangocms_versioning.helpers.{get_preview_url,get_editable_url}`
and the `djangocms_versioning.admin.{get_preview_url,get_editable_url}`
imports (`admin.py` binds them by name at module load) are rebound.

### 2. `middleware.py` — edit-mode URL redirect

`EditModeDefaultLanguageMiddleware.process_view` intercepts three CMS
admin URL names:

```
cms_placeholder_render_object_edit       — auto-create-draft allowed
cms_placeholder_render_object_structure  — auto-create-draft allowed
cms_placeholder_render_object_preview    — strict state-match, no side effects
```

When the targeted object's language is not the default, the middleware
rewrites the trailing object_id in `request.path` (the URL prefix `/de/` is
**preserved** so downstream `cms_path` queries keep the original language,
which `django-modeltranslation` and similar use to pick the right tab).

For edit and structure URLs, the swap target is computed by
`get_default_language_editable_sibling(content, user)`:

1. If a default-language DRAFT exists → return it.
2. Else if a default-language PUBLISHED exists →
   - Run versioning's own `Version.check_edit_redirect(user)` gate.
   - `Version.copy(user)` to create a new DRAFT atomically.
   - Return the new DRAFT's content.
3. Else → return `None` (no redirect; editor stays on the non-default-language
   page, but `cms.api.add_plugin` writes there will still have their plugin
   language pinned to default by the `pre_save` signal).

For preview URLs, `get_default_language_sibling` is used instead: strict
state-match, no auto-creation. Preview is read-only; creating drafts as a
side effect of viewing a preview URL would be surprising.

### 3. `signals.py` — two receivers

**`pre_cms_plugin_save`** — pre_save on `CMSPlugin`. Forces
`instance.language = DEFAULT_LANGUAGE` for every plugin save when the addon
is enabled. Catches programmatic writes that bypass the URL redirect (e.g.
`cms.api.add_plugin` from a management command, or
`djangocms_versioning`'s own cross-language copy).

**`cascade_publish_language_siblings`** — listens to
`djangocms_versioning.signals.post_version_operation` and fires for
`OPERATION_PUBLISH`. When any language sibling is published, it iterates
`utils.iter_cascade_targets(content)` and calls `draft_version.publish(user)`
on each yielded sibling. Constraints:

- A module-level `threading.local()` flag prevents the cascade from
  re-firing when its own publishes emit `post_version_operation` again.
- The user is pulled from `cms.utils.permissions.get_current_user()`
  (set by `cms.middleware.user.CurrentUserMiddleware`).
- `ConditionFailed` (and any other `publish()` exception) is caught
  per-sibling so a single failure never breaks the original publish.

The receiver is only connected when `djangocms_versioning` is installed.

### 4. `apps.py` — startup configuration check + deferred rebind

`GlobalUntranslatedPlaceholderConfig.ready()`:

- Connects a one-shot `request_started` signal that rebinds
  `cms.toolbar.toolbar`'s local references to the patched
  `get_object_edit_url` / `get_object_preview_url` /
  `get_object_structure_url`. Deferred because `cms.toolbar.toolbar`'s
  module-level code reads `apps.get_app_config('cms').cms_extension`,
  which isn't populated until `cms.ready()` runs.
- Raises `ImproperlyConfigured` when the addon is enabled but
  `EditModeDefaultLanguageMiddleware` is missing from `MIDDLEWARE`,
  or when it appears **before** `cms.middleware.toolbar.ToolbarMiddleware`.
  Skipped when the addon is off.

---

## The helpers (`utils.py`)

### `get_untranslated_default_language_if_enabled()`

Returns the language code in which plugins should be stored/rendered, or
`None` when the addon is off.

- `DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS = 'en'` → `'en'` (if `'en'` is
  in `settings.LANGUAGES`; otherwise falls back to `LANGUAGE_CODE`).
- `= True` → `settings.LANGUAGE_CODE`.
- Unset / `False` / `None` → `None`. The addon is disabled.

### `get_versionable_for(instance_or_model)`

Returns the registered `VersionableItem` for a content model/instance, or
`None` when djangocms-versioning is not installed or the model is not
registered as a versionable.

### `get_default_language_sibling(content, mirror_state_from=None)`

Strict-state sibling lookup used by the **renderer**. Three branches:

1. **Versioned + language-aware** (the common case): uses
   `VersionableItem.grouping_values(content)`, overrides `language` to the
   default, and queries `type(content)._base_manager`. When
   `mirror_state_from` is given and has versions, additionally filters by
   `versions__state=source_version.state` so draft-mode requests see the
   default-language draft and live-mode requests see the default-language
   published version.
2. **PageContent without versioning** (rare CMS-4-without-versioning
   setup): falls back to `page` + `language` filtering.
3. **Anything else** (non-versioned non-PageContent, versioned but not
   language-grouped, no `language` field): returns `None`. Modeltranslation-
   style "single record holds all languages" models land here intentionally
   — there's nothing to swap.

### `get_default_language_editable_sibling(content, user)`

Edit-side sibling lookup used by the **middleware**. Never returns a
PUBLISHED content directly — auto-creates a DRAFT via `Version.copy(user)`
when only PUBLISHED exists. Mirrors djangocms-versioning's own "New Draft"
button. Returns `None` when the user lacks permission, the existing draft
is locked by someone else, or the sibling doesn't exist at all.

### `iter_cascade_targets(content)`

Generator used by the cascade-publish receiver. Yields
`(sibling_language, draft_version)` tuples for every other-language sibling
of the same grouper that has BOTH a DRAFT Version and at least one
existing PUBLISHED Version.

The **"existing PUBLISHED required"** gate is the safety property: it
guarantees the cascade only keeps already-published languages in sync;
it never silently makes a brand-new language live.

---

## What works and what doesn't

### Works automatically

- `PageContent` (CMS 4 with versioning).
- `PageContent` (CMS 4 without versioning) — via the page+language fallback.
- Any registered `VersionableItem` whose `extra_grouping_fields` includes
  `"language"` (e.g. a custom `BlogPostContent`, `ArticleContent`, …).
- Models with multi-field grouping like
  `extra_grouping_fields=["language", "region"]` — only `language` is
  overridden; `region` (or any other extra grouping field) is preserved
  in the sibling lookup.

### Not handled (by design)

- **Modeltranslation-style "single record for all languages"** models —
  no `language` field, so there's nothing to swap. The `pre_save` signal
  still pins plugin language to the default, which is the desired behavior
  for these models.
- **Versioned models without language grouping** (e.g. a region-only
  versionable) — `iter_cascade_targets` and the sibling resolvers all
  return nothing for them.
- **Static placeholders / plugin-on-plugin sources** — `placeholder.source`
  is not a registered content model; the resolver returns the original
  placeholder unchanged.

### Other languages need to exist

For `/de/<slug>/` to be reachable, a `de` `PageContent` row needs to exist
and be published. The addon never creates `PageContent` rows for new
languages on its own. Editors create the `de` `PageContent` (with title,
slug, menu metadata) explicitly; from then on, the cascade keeps it in
sync with the default language's publish cycle.

---

## Edge cases & guarantees

- **URL prefix preservation in the redirect.** The middleware swaps only
  the trailing object_id; `/de/admin/cms/placeholder/object/<ct>/edit/<de_id>/`
  becomes `/de/admin/cms/placeholder/object/<ct>/edit/<en_id>/`. Downstream
  plugin-edit URLs encode this in their `cms_path` query string, which
  consumers like `django-modeltranslation` read to pick the right
  translation tab. Using `reverse()` (which would re-prefix the URL with
  `/en/`) is deliberately avoided.
- **URL prefix preservation in the structure board reload.** After a
  plugin save, the structure board reloads the page via `cms_edit_url`
  computed by `CMSToolbar.get_object_edit_url()`. Without the toolbar
  URL helper patch (see `models.py:_patch_toolbar_url_helpers`), the
  CMS rule `language = getattr(obj, "language", language)  # Object
  trumps parameter` forces the reload onto `/en/<...>` because the
  edited object is the en default-language sibling — silently flipping
  the editor into the English admin mid-session. The patch rewrites
  the URL's leading language segment to match the explicitly-requested
  language so the editor stays on `/de/`.
- **URL prefix preservation in the publish redirect** and the
  **new-draft redirect**. djangocms-versioning's `publish_view` and
  `edit_redirect_view` build their redirect URLs from
  `get_preview_url` / `get_editable_url`, both of which default to
  `content_obj.language` (always en under the addon). The CMS-side
  rewrite can't catch them because the language they pass matches
  `obj.language`. The `_patch_versioning_url_helpers()` patch makes
  both helpers use the request language
  (`django.utils.translation.get_language()`) when no explicit
  language is given, so the redirect target keeps the editor's URL
  prefix.
- **No live data mutation.** The editable-sibling lookup never returns
  a PUBLISHED content. CMS's own check `object_is_editable()` would
  redirect a PUBLISHED edit to a read-only preview; auto-create-draft
  short-circuits that case with a fresh draft instead.
- **Idempotent cascade.** Once a DRAFT is published, subsequent
  `post_version_operation` events find no DRAFT for that sibling and skip
  it. Concurrent first-time hits are safe because `Version.copy` and
  `Version.publish` both run `@transaction.atomic`.
- **Cascade does not silently make new languages live.** If a language has
  never been published, the gate in `iter_cascade_targets` filters it out
  even if a DRAFT exists. Editors keep full control over which languages
  are publicly reachable.
- **Re-entrance guard** on the cascade signal handler prevents recursive
  fan-out (N² explosion across N languages).
- **`Version.publish` is called with the current user**, pulled from
  `cms.utils.permissions.get_current_user()` (set by
  `cms.middleware.user.CurrentUserMiddleware`). If no current user is
  available (anonymous, management command, …), the cascade is skipped.

---

## Tests

Coverage lives in:

- `djangocms_misc/tests/test_untranslated_placeholders.py` — end-to-end
  HTTP test: en plugins render on `/de/<slug>/`; `add_plugin` from the de
  side gets language-pinned to en.
- `djangocms_misc/tests/test_generic_untranslated_placeholders.py` —
  unit + integration coverage for every helper and the four runtime
  components: sibling resolution (with and without versioning), renderer
  swap, edit-URL redirect (with and without auto-create), preview
  no-side-effect, modeltranslation-style models, region-only versionables,
  AppConfig configuration checks, and the cascade-publish gate semantics
  (existing PUBLISHED required, symmetric direction, addon-disabled,
  re-entrance guard).

Test app fixtures (`djangocms_misc/tests/test_app/`):

- `BlogPost` (grouper) + `BlogPostContent` — language-grouped versionable,
  exercises the generic path.
- `Note` — modeltranslation-style, no language field.
- `Region` + `RegionContent` — versionable without language grouping.

Run:

```sh
./manage.py test djangocms_misc.tests.test_untranslated_placeholders
./manage.py test djangocms_misc.tests.test_generic_untranslated_placeholders
```

---

## Programmatic API

For embeddings and custom views, the helpers in `utils.py` are stable:

```python
from djangocms_misc.global_untranslated_placeholder.utils import (
    get_untranslated_default_language_if_enabled,
    get_default_language_sibling,
    get_default_language_editable_sibling,
    iter_cascade_targets,
    get_versionable_for,
)

# Find the default-language equivalent of a non-default-language content
# row, mirroring the source's version state:
sibling = get_default_language_sibling(some_pagecontent, mirror_state_from=some_pagecontent)

# Get a draft to edit (creates one from PUBLISHED if needed):
editable = get_default_language_editable_sibling(some_pagecontent, request.user)

# Inspect the cascade-publish targets without publishing:
for language, draft_version in iter_cascade_targets(en_pagecontent):
    print(f'{language}: {draft_version.pk}')
```
