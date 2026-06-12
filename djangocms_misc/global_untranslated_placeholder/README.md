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
| Editing on `/de/<page>/?edit` | The middleware rewrites the URL to point at the en object id (URL prefix `/de/` is preserved); the editor edits the en placeholders directly. |
| Edit URL when only en PUBLISHED exists | A new en DRAFT is auto-created via `Version.copy(user)` (same code path as the toolbar's "New Draft" button), and the editor lands on the new draft. |
| Programmatic `cms.api.add_plugin(de_placeholder, ..., language='de')` | The plugin's `language` is force-pinned to `en` by a `pre_save` signal (defense in depth). |
| Toolbar actions (Edit / Neuer Entwurf, publish, unpublish, revert, archive, discard) | These POST-only views redirect to URLs built from `content.language` (always en), and the button URLs themselves can carry the wrong prefix (`reverse(...)` under `force_language(toolbar_language)`). The middleware rewrites the **response** Location's leading `/<lang>/` to match the editor's working language, stored in the session. |
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

**URL prefix handling is in `middleware.py`, not here.** Earlier
revisions of this addon also monkey-patched
`cms.toolbar.utils.get_object_{edit,preview,structure}_url` and
`djangocms_versioning.helpers.{get_preview_url,get_editable_url,
get_object_live_url}` to keep the editor's URL prefix consistent across
publish / new-draft / preview redirects. Those patches were brittle
(each consumer of the helpers captured the function object at its own
import time, requiring per-module late rebinding) and have been
**removed**. The middleware's path-rewrite is the only mechanism that
keeps the URL prefix correct now — see `middleware.py` below.

### 2. `middleware.py` — edit-mode URL redirect

`EditModeDefaultLanguageMiddleware` is built around one idea: **the
session is the single source of truth for the language the editor is
working in.**

```
__call__ (response phase):
  staff GET on a frontend URL (/de/<page>/)    — write session[SESSION_LANGUAGE_KEY] = 'de'
  that rendered successfully (no redirect/error)

process_view (GET render endpoints):
  cms_placeholder_render_object_edit           — session-language redirect (XHR only),
  cms_placeholder_render_object_structure       then object-id swap (auto-create-draft)
  cms_placeholder_render_object_preview        — session-language redirect (any request),
                                                 then object-id swap (strict state-match)

__call__ (response phase, POST-only versioning actions):
  *_edit_redirect / *_publish / *_unpublish    — rewrite redirect Location to session language
  *_revert / *_archive / *_discard               (LANGUAGE_REDIRECT_URL_SUFFIXES)
```

**Session write rule** (`_store_session_language`). The value is written
ONLY on deliberate, successful frontend navigation: an authenticated
**staff** GET on a **non-admin** URL with a valid language prefix whose
response actually rendered (success status — checked after
`get_response`, so requests that end in a redirect, e.g. CMS's
language-fallback redirect, or an error page never write). Consequences:

- Corrupted admin/endpoint URLs (the ones the read paths fix) can never
  poison the session. A Referer-based variant existed before; the
  Referer faithfully reports the poisoned address bar after the
  structure board's `history.replaceState` — the session does not.
- Language switching happens through the frontend's language links:
  visiting `/en/<page>/` updates the session to `'en'`.
- Anonymous visitors never get a session written (no session-cookie
  churn / cache busting on the public site).

**Placeholder edit/structure/preview URLs.** Two steps, in order:

1. *Session-language redirect* (`_redirect_to_session_language` — THE
   language-prefix helper). The CMS URL helpers apply
   `language = getattr(obj, "language", language)` (object trumps
   parameter), so on `/de/.../edit/<en_pk>/` the toolbar's Preview
   button, the Structure/Content switcher, and the `CMS.config`
   `edit`/`edit_off`/`structure` URLs (used by the post-save
   structure-board XHR reload, and pushed into the address bar by
   `history.replaceState` when toggling structure mode) all come out
   as `/en/...`. When the request's URL prefix differs from the
   session language, 302 to the same path under the session language —
   safe for these GET endpoints (no POST→GET method change). Edit and
   structure URLs are only rewritten for **XHR** requests
   (`X-Requested-With: XMLHttpRequest`, or `Sec-Fetch-Mode` other than
   `navigate`) — a top-level navigation to an edit URL may be
   deliberate. Preview URLs are rewritten for **any** request; the
   toolbar language menu (which links to other languages' preview
   URLs) consequently can't switch languages — switching happens via
   frontend language links.

2. *Object-id swap.* When the targeted object's language is not the
   default, rewrite the trailing `object_id` in `request.path` (the
   URL prefix `/de/` is **preserved** so downstream `cms_path` queries
   keep the original language, which `django-modeltranslation` and
   similar use to pick the right tab).

**Versioning action URLs** (`*_edit_redirect` / `*_publish` /
`*_unpublish` / `*_revert` / `*_archive` / `*_discard`). These views
are POST-only (versioning's admin returns 405 to GET) and their
post-action redirect URL is built from `version.content.language` via
`get_editable_url` / `get_preview_url` / `get_object_live_url` /
`version_list_url`. Under this addon the editor is editing the
default-language sibling (`content.language = 'en'`), so those
redirects always land on `/en/...` regardless of the URL prefix the
editor was actually on. (The action button URLs themselves can also
carry the wrong prefix — the toolbar builds them via `reverse(...)`
under `force_language(self.toolbar_language)`, a user UI preference.)

We can't 302 the inbound POST — a 302 would convert POST→GET and
versioning would return 405. The middleware instead rewrites the
**response** Location: when the response is a 3xx for one of these
action URL names and its Location's leading language segment differs
from the **session language**, we swap the Location's leading
`/<lang>/` to match the session. For Edit / Neuer Entwurf this means
the POST goes straight through versioning's `edit_redirect_view` (no
inbound interference), and only the outgoing `/en/.../edit/<en_pk>/`
Location is swapped to `/<session_lang>/.../edit/<en_pk>/`. This
rewrite also matters for `ON_PUBLISH_REDIRECT="published"`, where the
Location is a frontend URL — landing on `/en/<slug>/` unrewritten
would flip the session on the very next request.

No-op when: the URL name doesn't match, the response is not a 3xx,
the Location has no recognised leading language segment, no (valid)
session language is stored, or the session language already matches
the Location's.

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

### 4. `apps.py` — startup configuration checks

`GlobalUntranslatedPlaceholderConfig.ready()` runs only when the addon
is enabled (`DJANGOCMS_MISC_UNTRANSLATED_PLACEHOLDERS` is truthy) and
raises `ImproperlyConfigured` on misconfiguration:

- **Middleware presence** — `EditModeDefaultLanguageMiddleware` must
  appear in `MIDDLEWARE`.
- **Middleware ordering** — it must come **after**
  `cms.middleware.toolbar.ToolbarMiddleware` (the redirect handler
  reads `request.toolbar`, which the CMS middleware sets up).

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
- **The session is the language source of truth.** Written only on
  deliberate frontend navigation (staff GET, non-admin URL, valid
  prefix); read by every rewrite path. This survives the structure
  board's `history.replaceState` (which silently flips the address
  bar — and thereby the Referer — to the default-language URL baked
  into `CMS.config.settings`): no request happens during
  `replaceState`, so the session keeps the editor's real language.
  Multi-tab caveat: one value per editor — the last frontend
  navigation wins across tabs.
- **Versioning action redirect targets** (Edit / Neuer Entwurf,
  publish, unpublish, revert, archive, discard). These views are
  POST-only and their post-action redirect URLs are built from
  `content.language` (always en under this addon), so the editor
  would always land on `/en/...`. The toolbar's button URLs can also
  carry the wrong prefix (built via `reverse(...)` under
  `force_language(self.toolbar_language)` — a user UI preference).
  We can't 302 the inbound POST (a 302 → GET would yield a 405). The
  middleware rewrites the **response** Location instead: when the
  response is a 3xx and its Location's leading language segment
  differs from the session language, swap Location's leading
  `/<lang>/` to match the session. We do NOT touch `toolbar_language`
  itself — it remains a user preference for the toolbar UI.
- **Toolbar GET URLs (Preview button, mode switcher, XHR reload).**
  Same wrong-prefix problem, handled inbound (safe for GET): requests
  to the placeholder render endpoints whose URL prefix differs from
  the session language are 302'd to the session language — edit
  and structure only when the request is an XHR, preview always. The
  known trade-off: the toolbar language menu links to other languages'
  preview URLs, so it can't switch languages — the frontend's language
  links are the way to switch (they update the session).
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
