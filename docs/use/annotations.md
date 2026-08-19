---
doc_type: how-to
audience:
  - ci-owner
level: intermediate
canonical_for:
  - pr-annotations
lifecycle: active
generated: false
---

# GitHub PR Annotations

abicheck can emit [GitHub Actions workflow command annotations][gh-wc] so that
ABI breaking changes appear as **inline comments directly on PR diffs**. Errors,
warnings, and notices are pinned to the exact file and line where the change
was detected.

[gh-wc]: https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions

## Quick start

Set `annotate: true` on the composite Action:

```yaml
- name: Check ABI compatibility
  uses: abicheck/abicheck@v0.5.0
  with:
    old-library: abi-baseline.json
    new-library: build/libfoo.so
    new-header: include/foo.h
    annotate: true
```

That's it. On the next PR, any breaking change detected by abicheck will show
up as a red error annotation on the changed file in the PR diff view, and a
Markdown summary will appear in the **Job Summary** panel.

## How it works

The CLI's `compare`/`compare-release` no longer render annotations
themselves at all — every `compare --format json` report persists a
top-level `annotations` array (see "Persisted alongside the report" below),
computed unconditionally regardless of any flag or input. The composite
Action reads that array straight off the report and prints
[workflow command annotations][gh-wc] itself
(`action/run.sh`'s `_emit_annotations`), gated on two Action inputs:

1. `annotate: true` — print the always-visible entries (errors, warnings,
   and the one unconditional notice: a `--contract` finding compatibility
   policy never evaluated).
2. `annotate-additions: true` — also print the opt-in notices (additions,
   quality issues, other `info`-severity findings). Requires `annotate:
   true` to have any effect.

Annotations print to the Action's own log output (stdout), so GitHub Actions
processes them from the same step that ran the comparison. This works
identically for a single-pair `compare` and a directory/package (release)
`compare` — the Action reads `libraries[].annotations` for the latter and
flattens across every library.

If `$GITHUB_STEP_SUMMARY` is available (automatic on GitHub Actions
runners), the composite Action separately appends a Markdown summary to the
[Job Summary][gh-summary] panel via its own `add-job-summary` input — see
[GitHub Action Inputs](../reference/github-action-inputs.md). This is
independent of `annotate`/`annotate-additions`.

[gh-summary]: https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions#adding-a-job-summary

### Persisted alongside the report (`--format json`)

Since report schema 2.43, every `compare --format json` report carries
a top-level `annotations` array — one already-classified,
already-formatted entry (`{"level": "error"|"warning"|"notice",
"annotation": "::error file=...,line=...,title=...::message",
"always_visible": true}`) per finding a full annotation pass over the
comparison found, always the superset (as if `annotate-additions` had
also been requested). A directory/package (release) `compare` persists the
identical shape per library, at `libraries[].annotations`.

A consumer deciding whether to keep a `"notice"`-level entry must gate on
`always_visible` (schema 2.44), not on `level` alone: one notice kind — a
`--contract` finding compatibility policy never evaluated — is shown even
without `annotate-additions`, so it carries `always_visible: true`; every
other notice (an addition, a quality issue, an `info`-severity finding)
only exists because this array always computes the `annotate-additions`
superset, and carries `always_visible: false`. `always_visible` is always
`true` for `"error"`/`"warning"`. This is what the composite Action's
renderer reads instead of parsing stderr or re-running the comparison —
see [`docs/reference/exit-codes.md`](../reference/exit-codes.md)'s sibling
`exit` field for the same pattern applied to the gate decision.

## Severity mapping

| Change category | Annotation level | Annotation title prefix | Enabled by default |
|-----------------|-----------------|------------------------|--------------------|
| BREAKING (binary ABI incompatible) | `::error` | `ABI Break: <kind>` | Yes |
| API_BREAK (source-level break) | `::warning` | `API Break: <kind>` | Yes |
| COMPATIBLE_WITH_RISK (deployment risk) | `::warning` | `Deployment Risk: <kind>` | Yes |
| COMPATIBLE (additions, quality issues) | `::notice` | `ABI Addition: <kind>` | Only with `annotate-additions: true` |

### Example annotation output

```text
::error file=include/foo.h,line=42,title=ABI Break%3A func_params_changed::Parameter 1 of foo::baz changed from int to long (binary incompatible)
::warning file=include/foo.h,line=15,title=API Break%3A enum_member_renamed::Enum member renamed: kOld -> kNew
::warning title=Deployment Risk%3A symbol_version_required_added::New GLIBC_2.34 version requirement added
::notice title=ABI Addition%3A func_added::Function foo::new_thing() was added to the public interface
```

## Action inputs

### `annotate`

Emit GitHub Actions workflow command annotations for the always-visible
entries (errors, warnings, and the one unconditional "not evaluated"
notice). Default `false`.

### `annotate-additions`

Also emit the opt-in notices (additions and compatible changes). Off by
default because additions are typically informational and can be noisy.
Has no effect without `annotate: true`.

## Usage examples

### Basic: annotate breaking changes on PRs

```yaml
name: ABI Check
on: [pull_request]

jobs:
  abi-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build library
        run: mkdir build && cd build && cmake .. && make

      - name: Check ABI compatibility
        uses: abicheck/abicheck@v0.5.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          annotate: true
```

### Include additions as notices

```yaml
      - name: Check ABI compatibility
        uses: abicheck/abicheck@v0.5.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          annotate: true
          annotate-additions: true
```

### Annotate a release comparison

```yaml
      - name: Compare RPM packages
        uses: abicheck/abicheck@v0.5.0
        with:
          old-library: libfoo-1.0-1.el9.x86_64.rpm
          new-library: libfoo-1.1-1.el9.x86_64.rpm
          annotate: true
```

### Combine with SARIF upload

Annotations and SARIF are complementary: annotations give immediate inline
feedback on the PR diff, while SARIF populates the Security tab with
persistent alerts.

```yaml
      - name: Check ABI compatibility
        uses: abicheck/abicheck@v0.5.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          format: sarif
          upload-sarif: true
          annotate: true
```

### Reading annotations without the composite Action

If you invoke the `abicheck` CLI directly (not through
`abicheck/abicheck@...`), render annotations yourself from the persisted
`annotations` report field — the CLI itself has no `--annotate` flag to
pass:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=v1/foo.h --header new=v2/foo.h \
  --format json --output report.json

python3 -c '
import json, sys
report = json.load(open("report.json"))
# The persisted array is intentionally uncapped -- sort by severity and
# apply the same 50-per-step cap the composite Action itself applies
# (action/run.sh), or a large report can exceed GitHub Actions own limit
# on visible annotations per step. `always_visible` is schema 2.44+; a
# report from schema 2.43 (when `annotations` itself was introduced) has
# no such key on each entry -- degrade to "visible unless it is a
# notice", the same fallback action/run.sh itself uses for an older report.
order = {"error": 0, "warning": 1, "notice": 2}
visible = sorted(
    (
        e for e in report.get("annotations", [])
        if e.get("always_visible", e.get("level") != "notice")
    ),
    key=lambda e: order.get(e["level"], 99),
)
for e in visible[:50]:
    print(e["annotation"])
'
```

## Behavior details

### Source location

Annotations include `file=` and `line=` properties only when abicheck has
source location information for the change. This is available when:

- Headers are provided (`-H`/`--header`, side-aware `old=`/`new=`)
- DWARF debug info is present in the binary
- BTF/CTF metadata is available

In **symbols-only mode** (no headers, no debug info), annotations are still
emitted but without file/line — they appear as step-level annotations rather
than inline on the diff.

### Annotation limit

GitHub Actions caps visible annotations at approximately 50 per step. The
persisted `annotations` array itself is intentionally **uncapped** —
`annotation_report_entries()` returns every classified finding, since a
persisted report is a general-purpose artifact other consumers (SARIF,
JUnit, a custom script) may want in full. It is the composite Action's own
renderer (`action/run.sh`'s `_emit_annotations`) that sorts entries by
severity and applies the 50-per-step cap so the most important ones
(errors first, then warnings, then notices) are always visible — a
renderer other than the Action, including the "reading annotations without
the composite Action" example above, must apply the same cap itself before
emitting real `::error`/`::warning`/`::notice` workflow commands, or risk
exceeding GitHub's limit.

For a bundle `compare` (directory/package inputs), the composite Action's
50-annotation budget is shared across all libraries in the release. This
ensures a single noisy library doesn't consume all available annotation
slots.

### Message truncation

Annotation messages are truncated to 200 characters to stay within GitHub's
undocumented message length limits. Long descriptions end with `...`.

### Job Summary

The composite Action's own `add-job-summary` input (default `true`)
appends a Markdown ABI report to the Job Summary panel, independent of
`annotate`/`annotate-additions` — see
[GitHub Action Inputs](../reference/github-action-inputs.md).

- **single-library `compare`**: writes the per-library Markdown report
- **bundle `compare`** (directory/package inputs): writes the consolidated release summary (one entry,
  not per-library)

### Special character escaping

Annotation property values (file, line, title) escape `:`, `,`, `%`, `\n`,
and `\r` using GitHub's `%`-encoding. Message bodies escape `%`, `\n`, and
`\r` only (colons are safe in the message portion).

## Comparison with other annotation methods

| Method | Inline on diff | Persistent | Setup |
|--------|---------------|------------|-------|
| **`annotate: true`** (this feature) | Yes | No (per-run) | Add one Action input |
| **SARIF + Code Scanning** | Yes (Security tab) | Yes (alerts) | `format: sarif` + `upload-sarif: true` + permissions |
| **Job Summary** | No (separate panel) | No (per-run) | Automatic via `add-job-summary` |
| **Markdown report** (default) | No (log output) | No | Default behavior |

For most teams, `annotate: true` provides the best signal-to-noise ratio with
zero configuration beyond the single input.

## Troubleshooting

### Annotations not appearing

1. **Is `annotate: true` set?** Check the Action's `with:` block in your
   workflow YAML.
2. **Running through the composite Action?** A raw `abicheck compare` CLI
   invocation outside `abicheck/abicheck@...` has no annotation renderer of
   its own — read the persisted `annotations` report field yourself (see
   "Reading annotations without the composite Action" above).
3. **Are there any changes?** No annotations are emitted for `NO_CHANGE` results.
4. **File path mismatch?** Annotations with `file=` are only shown inline when
   the file path matches a file changed in the PR. Step-level annotations
   (without file/line) always appear in the Actions log.
5. **Hit the 50-annotation limit?** If you have more than 50 issues, lower-severity
   ones are dropped. Use `--format json` or check the Job Summary for the
   complete list.

### Annotations appear but not inline

This happens when `source_location` is not available (symbols-only mode). To
get inline annotations, provide headers (`-H`) or ensure DWARF debug info is
present in the binary.

### Too many notice annotations

Use `annotate: true` without `annotate-additions: true` (the default). This
limits annotations to breaking changes, warnings, and the one unconditional
"not evaluated" notice (see "How it works" above).

## Migrating from `extra-args: --annotate`

Older workflows passed `--annotate`/`--annotate-additions` to the CLI via the
Action's `extra-args` input. Those CLI flags have been removed —
`abicheck compare --annotate` now exits `64` with `No such option`. Replace:

```diff
- extra-args: --annotate --annotate-additions
+ annotate: true
+ annotate-additions: true
```
