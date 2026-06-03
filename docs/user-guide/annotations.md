# GitHub PR Annotations

abicheck can emit [GitHub Actions workflow command annotations][gh-wc] so that
ABI breaking changes appear as **inline comments directly on PR diffs**. Errors,
warnings, and notices are pinned to the exact file and line where the change
was detected.

[gh-wc]: https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions

## Quick start

Add `--annotate` to your existing abicheck step:

```yaml
- name: Check ABI compatibility
  uses: napetrov/abicheck@v0.3.0
  with:
    old-library: abi-baseline.json
    new-library: build/libfoo.so
    new-header: include/foo.h
    extra-args: --annotate
```

That's it. On the next PR, any breaking change detected by abicheck will show
up as a red error annotation on the changed file in the PR diff view, and a
Markdown summary will appear in the **Job Summary** panel.

## How it works

When both conditions are met:

1. The `--annotate` flag is passed, **and**
2. The environment variable `GITHUB_ACTIONS=true` is set (automatic on all
   GitHub Actions runners)

abicheck prints [workflow command annotations][gh-wc] to **stderr** so that the
primary stdout payload (JSON, SARIF, HTML, Markdown) remains a clean,
machine-parsable stream. GitHub Actions processes workflow commands from both
stdout and stderr, so annotations work correctly on both channels.

If `$GITHUB_STEP_SUMMARY` is available (also automatic on GitHub Actions
runners), abicheck appends a full Markdown ABI report to the
[Job Summary][gh-summary] panel.

[gh-summary]: https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions#adding-a-job-summary

## Severity mapping

| Change category | Annotation level | Annotation title prefix | Enabled by default |
|-----------------|-----------------|------------------------|--------------------|
| BREAKING (binary ABI incompatible) | `::error` | `ABI Break: <kind>` | Yes |
| API_BREAK (source-level break) | `::warning` | `API Break: <kind>` | Yes |
| COMPATIBLE_WITH_RISK (deployment risk) | `::warning` | `Deployment Risk: <kind>` | Yes |
| COMPATIBLE (additions, quality issues) | `::notice` | `ABI Addition: <kind>` | Only with `--annotate-additions` |

### Example annotation output

```text
::error file=include/foo.h,line=42,title=ABI Break%3A func_params_changed::Parameter 1 of foo::baz changed from int to long (binary incompatible)
::warning file=include/foo.h,line=15,title=API Break%3A enum_member_renamed::Enum member renamed: kOld -> kNew
::warning title=Deployment Risk%3A symbol_version_required_added::New GLIBC_2.34 version requirement added
::notice title=ABI Addition%3A func_added::Function foo::new_thing() was added to the public interface
```

## CLI flags

Both `compare` and `compare-release` commands support these flags:

### `--annotate`

Emit GitHub Actions workflow command annotations to stderr. Annotations appear
as inline comments on PR diffs. Only effective when `GITHUB_ACTIONS=true`.

When not running inside GitHub Actions, this flag is silently ignored — you can
leave it in your CI config and run the same command locally without side effects.

### `--annotate-additions`

Include additions and compatible changes as `::notice` annotations. Off by
default because additions are typically informational and can be noisy.

Requires `--annotate`. Passing `--annotate-additions` without `--annotate`
produces an error:

```
Error: --annotate-additions requires --annotate
```

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
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          extra-args: --annotate
```

### Include additions as notices

```yaml
      - name: Check ABI compatibility
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          extra-args: --annotate --annotate-additions
```

### Annotate a release comparison

```yaml
      - name: Compare RPM packages
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: compare-release
          old-library: libfoo-1.0-1.el9.x86_64.rpm
          new-library: libfoo-1.1-1.el9.x86_64.rpm
          extra-args: --annotate
```

### Combine with SARIF upload

Annotations and SARIF are complementary: annotations give immediate inline
feedback on the PR diff, while SARIF populates the Security tab with
persistent alerts.

```yaml
      - name: Check ABI compatibility
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
          format: sarif
          upload-sarif: true
          extra-args: --annotate
```

### Local CLI usage (no annotations emitted)

When running locally, `--annotate` is a no-op since `GITHUB_ACTIONS` is not
set:

```bash
# Annotations silently skipped (GITHUB_ACTIONS not set)
abicheck compare libfoo.so.1 libfoo.so.2 \
  --old-header v1/foo.h --new-header v2/foo.h \
  --annotate

# To test annotation output locally, set the env var:
GITHUB_ACTIONS=true abicheck compare libfoo.so.1 libfoo.so.2 \
  --old-header v1/foo.h --new-header v2/foo.h \
  --annotate
```

## Behavior details

### Source location

Annotations include `file=` and `line=` properties only when abicheck has
source location information for the change. This is available when:

- Headers are provided (`-H`, `--old-header`, `--new-header`)
- DWARF debug info is present in the binary
- BTF/CTF metadata is available

In **symbols-only mode** (no headers, no debug info), annotations are still
emitted but without file/line — they appear as step-level annotations rather
than inline on the diff.

### Annotation limit

GitHub Actions caps visible annotations at approximately 50 per step.
abicheck enforces this limit and sorts annotations by severity so the most
important ones (errors first, then warnings, then notices) are always visible.

For `compare-release`, the 50-annotation budget is shared across all libraries
in the release. This ensures a single noisy library doesn't consume all
available annotation slots.

### Message truncation

Annotation messages are truncated to 200 characters to stay within GitHub's
undocumented message length limits. Long descriptions end with `...`.

### Job Summary

When `$GITHUB_STEP_SUMMARY` is available, abicheck automatically appends a
full Markdown ABI report to the Job Summary panel. This provides the complete
report alongside the inline annotations.

- **`compare`**: writes the per-library Markdown report
- **`compare-release`**: writes the consolidated release summary (one entry,
  not per-library)

### Special character escaping

Annotation property values (file, line, title) escape `:`, `,`, `%`, `\n`,
and `\r` using GitHub's `%`-encoding. Message bodies escape `%`, `\n`, and
`\r` only (colons are safe in the message portion).

## Comparison with other annotation methods

| Method | Inline on diff | Persistent | Setup |
|--------|---------------|------------|-------|
| **`--annotate`** (this feature) | Yes | No (per-run) | Add one flag |
| **SARIF + Code Scanning** | Yes (Security tab) | Yes (alerts) | `format: sarif` + `upload-sarif: true` + permissions |
| **Job Summary** | No (separate panel) | No (per-run) | Automatic with `--annotate` |
| **Markdown report** (default) | No (log output) | No | Default behavior |

For most teams, `--annotate` provides the best signal-to-noise ratio with zero
configuration beyond the single flag.

## Troubleshooting

### Annotations not appearing

1. **Is `--annotate` set?** Check `extra-args` in your workflow YAML.
2. **Running on GitHub Actions?** Annotations require `GITHUB_ACTIONS=true`.
   Self-hosted runners set this automatically.
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

Use `--annotate` without `--annotate-additions` (the default). This limits
annotations to breaking changes and warnings only.
