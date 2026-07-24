---
doc_type: contributor
audience:
  - contributor
level: intermediate
lifecycle: active
generated: false
---

# Writing documentation for abicheck

`docs/AGENTS.md` is the normative contract — what's required, what
`scripts/check_docs_contract.py` enforces, the front-matter schema. This page
is its human-readable companion: *why* the structure is the way it is, what a
new page should look like, and how to tell when a change is actually done.
Read `docs/AGENTS.md` first if you haven't; this page assumes it.

## Why one fact needs one owner

Documentation drifts the same way code does — a table gets copied instead of
linked, a second explanation grows next to the first, and six months later
nobody knows which one is current. The fix isn't "review harder," it's making
ownership explicit enough that a gate can check it:

- `docs/_meta/topics.yaml` says which page is the *canonical* explanation of a
  topic, which page is its *worked example*, which pages are *task pages*
  built on it, and which pages may carry a short *summary*.
- `scripts/check_docs_contract.py` enforces the structural half of that (no
  two owners, no orphaned claims) and flags likely accidental duplication
  (an identical 40+ word block in two pages) as a warning.

Neither of those catches "this page re-explains a concept another page
already owns, just in different words." That's a review judgment call — this
page exists to make that call easier to make consistently.

## Picking the right shape for new content

A new feature does not need a new page — see `docs/AGENTS.md`'s "When does a
new fact need a new page?" for the actual decision rule. If you do need one,
pick the shape that matches the question it answers:

### Tutorial

Answers *"walk me through doing this end to end."* Sequential, one path, no
mid-tutorial forks.

```text
# <Outcome-oriented title>
Outcome
Prerequisites
Step 1 / Step 2 / Step 3 …
Verify the result
Next step
```

### How-to

Answers *"how do I do this one specific thing?"* Assumes the reader already
has the mental model; links to the explanation page instead of re-deriving it.

```text
# <Task-oriented title>
When to use this
Procedure
Expected result
Related concepts / Reference
```

### Explanation

Answers *"why does this work this way?"* Owns the mental model for a topic —
if `docs/_meta/topics.yaml` has a `canonical_page` for this subject, this is
its role.

```text
# <Concept name>
In one minute
Mental model / mechanism
Boundaries — what this does and doesn't cover
Related pages
```

### Reference

Answers *"what are the exact values?"* Exhaustive, generated where possible
(`scripts/gen_detector_spec.py`, `scripts/gen_repo_facts.py`, …). No
narrative, no recommendations — those belong on an explanation or how-to page
that links here.

### Hub

Answers *"where do I go for X?"* A hub is a signpost, not a fourth copy of
its children's content — if you're tempted to add a paragraph explaining
something instead of linking to the page that owns it, that paragraph
probably belongs on the child page.

### Migration

Answers *"what changed, and what do I do about it?"* Owns *historical*
information — a removed command or option is described here, not in the
current user guide. Old URLs redirect here (see "Retiring a page" below).

### Case (`examples/case*/README.md`)

Answers *"show me one concrete break and how abicheck sees it."* Generated
into `docs/examples/*.md` by `scripts/gen_examples_docs.py` — edit the
`examples/caseNN/README.md` source, not the generated page.

## What a duplication fix actually looks like

Three real examples from this repo, so "don't duplicate" has concrete shape
instead of being an abstract rule:

**Exact table, two pages, no reason for both.** `user-guide/severity.md` used
to carry its own copy of the severity-aware exit-code table and the presets
table — both already owned by `reference/exit-codes.md` (registered as the
`verdicts` topic's `reference_page`). The fix wasn't to explain severity
differently on the CI-gating page; it was to delete the copy and link to the
one table that exists:

```diff
-| Exit code | Meaning |
-|-----------|---------|
-| `0` | No error-level findings |
-...
+See [Severity-aware exit codes](../reference/exit-codes.md#severity-aware-exit-codes-with-any-severity-flag)
+for the exact code-to-condition table; the highest applicable code wins.
```

**Same fact, two different framings.** `concepts/architecture.md` and
`concepts/limitations.md` both carried the platform-support matrix table —
one as a quick "here's what's supported" aside, one as the dedicated
"Platform support matrix" section with the fuller explanatory prose.
Rather than pick one to delete outright, the fuller version stayed as the
registered `canonical_page` for a new `platform-support-matrix` topic, and
the aside became a one-line summary + link.

**A concept explained a fourth time.** `user-guide/tool-modes.md` carried its
own full copy of the L0–L4 evidence-layer table — the same model already
explained by the deliberate three-page trio (`concepts/evidence-and-detectability.md`,
`concepts/what-each-level-sees.md`, `user-guide/scan-levels.md`). The fix was
a "Quick decision" one-liner plus links back to the trio, and registering the
page as an `allowed_summaries` entry so the next person who touches it can
see, from `topics.yaml` alone, that a full table doesn't belong there.

The pattern in all three: find the real owner, delete the copy, leave a link
(and a one-line summary if the page genuinely needs orientation, not the
full explanation).

## Document lifecycle

Front matter's `lifecycle` field is one of:

- **`active`** — current, maintained, the default.
- **`migration`** — describes a transition (see "Migration" above); expected
  to eventually become historical once the transition window closes.
- **`historical`** — kept for reference (an old ADR, a superseded design)
  but not part of the current user-facing story; not linked from primary nav.

There's no automatic archival — a page's `lifecycle` is a manual, reviewed
decision, same as any other front-matter field.

## Retiring or merging a page

1. Decide the survivor: which page has the clearer question, the more stable
   URL, fewer mixed responsibilities, more inbound links, and less
   version-specific content.
2. Move any content unique to the page being retired onto the survivor —
   don't just delete it. Everything else becomes a link.
3. Update every inbound reference (`grep -rn "old-page.md" docs/ mkdocs.yml`)
   to point at the survivor.
4. Remove the retired page from `mkdocs.yml`'s `nav`.
5. Add a `redirect_maps` entry under the `redirects` plugin in `mkdocs.yml`
   so old URLs (bookmarks, search results) keep resolving:
   ```yaml
   plugins:
     - redirects:
         redirect_maps:
           user-guide/old-page.md: user-guide/new-home.md
   ```
6. Delete the old file. Don't leave a stub page "just in case" — the
   redirect covers that.
7. If the retired page owned a `docs/_meta/topics.yaml` topic, update the
   registry to point at the new home.
8. Run `python scripts/check_docs_contract.py` and
   `mkdocs build --strict` — both must stay clean.

## PR checklist for a documentation change

- [ ] What compatibility contract or user task changed, concretely?
- [ ] Does an existing page already own this fact or topic
      (`docs/_meta/topics.yaml`)? Extend it rather than adding a new page.
- [ ] Did you copy a table, count, or command example that already has a
      fact owner elsewhere? Link instead.
- [ ] If you added/changed a `canonical_page`, `worked_example`, `task_pages`,
      or `allowed_summaries` entry, did you update `docs/_meta/topics.yaml` to
      match?
- [ ] Does the page have front matter, and does its `doc_type` match what
      it actually is (see the shapes above)?
- [ ] `python scripts/check_docs_contract.py` — 0 errors?
- [ ] `mkdocs build --strict` — no new dangling links or orphan warnings?
- [ ] If you removed or renamed a page, did you add a redirect (see
      "Retiring or merging a page") and update every inbound link?

`scripts/verify.py --profile pr` runs the machine-checkable parts of this
list; the checklist above is what a reviewer should still eyeball by hand.
