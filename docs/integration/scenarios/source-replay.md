# Scenario S7: Source Scan via Compile-DB Replay

You have a compile database (or something abicheck can derive one from) and
want PR-scoped source-level checks — inline function bodies, template
instantiations, macro values, default arguments, `constexpr` — not just the
binary/header surface. [ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8's S7: on a pull request, scope the (expensive) replay to just the changed
translation units; on a release/nightly run, replay the whole target.

This is the **replay** evidence producer — abicheck derives source facts
itself from your existing compile database, with no build-time integration
step. Compare with [S8/S9: Source Facts From the Build Itself](build-integrated-facts.md),
where the *build* emits source facts as it compiles instead.

## What you need

- A source checkout (`sources:`).
- A compile database, or something abicheck can derive one from
  zero-config (CMake configure-only, a Bazel `aquery`, or a Make dry-run
  transcript) — see
  [Source-Scan Depth § Obtaining a compile database](../../use/scan-levels.md#obtaining-a-compile-database-without-a-full-build).
- `clang` on the runner. On the latest published release (`@v0.5.0`, used
  below), the Action's only installer is the legacy `install-deps: true`
  path — on an apt-based Linux runner (e.g. `ubuntu-latest`) this installs
  clang alongside castxml, no extra input needed; on macOS, clang comes
  from the runner's preinstalled Xcode toolchain rather than from
  `install-deps` itself; on Windows, this path installs neither castxml nor
  clang, so a Windows runner needs one installed manually before L4 replay
  will run. On a newer pin (`dependency-source` and its
  `conda-forge-clang20` value are not in `@v0.5.0` — see the note on the
  `check-target` example below), the default `dependency-source: conda-forge`
  does **not** install clang on Linux/macOS either; pin
  `dependency-source: conda-forge-clang20` (or `system`, or install clang
  yourself) instead. Without clang, L3 build-context evidence (this
  scenario's compile database) is still collected — only the clang-dependent
  L4 replay and L5 graph stages are skipped, not the whole source scan; see
  [Source-Scan Depth](../../use/scan-levels.md) for what each layer needs.
  Check the `layers`/coverage output to confirm L4 actually ran. See
  [GitHub Action: dependency-source](../../use/github-action.md).
- For a PR run: `fetch-depth: 0` on checkout, so the base ref is available to
  seed the diff scope.

## Two entry points, same evidence

**One-step, root-Action `mode: scan`** — the simplest wiring, classifies
changed paths, runs the pinned evidence level, and compares in one step:

```yaml
- uses: abicheck/abicheck@v0.5.0
  with:
    mode: scan
    new-library: build/libfoo.so
    new-header: include/
    sources: .
    against: abi-baseline.json
    since: origin/${{ github.base_ref }}
```

See [GitHub Action: Source Scans](../../use/github-action-source-scans.md)
for every input this mode accepts, cost estimation, and gating on a specific
cross-source check.

**Composed via `check-target`/`check-project.yml`** — when this check is one
of several a `.abicheck.yml` `targets:`/`profiles:` block declares,
`evidence-producer: replay` is the bridge. `actions/check-target` shipped
after the `v0.5.0` release, so pin a commit SHA (or `@main`) instead of a
release tag until the next release includes it:

```yaml
- uses: abicheck/abicheck/actions/check-target@c9e135a3233b6d45e9571533f71293fde458a469  # not yet in a tagged release; pin main or newer
  with:
    name: libfoo
    requested-depth: source
    evidence-producer: replay
    sources: .   # replay's own default when omitted -- a bare pointer, no build step needed
    # ... baseline/candidate inputs per your baseline channel ...
```

See the [`check-target` reference](../../reference/check-target.md) for the
full `evidence-producer` contract and how it composes with `collect-facts`.

## When to move past this scenario

- **Your build can emit source facts itself, avoiding a second replay pass**
  → [S8/S9: Source Facts From the Build Itself](build-integrated-facts.md)
  (the `abicheck-cc` wrapper or the Clang plugin).
- **You have several DSOs sharing one facts pack** → S16,
  [Build Info & Sources](../../learn/build-source-data.md).

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [GitHub Action: Source Scans](../../use/github-action-source-scans.md) — the canonical `mode: scan` reference.
- [Source-Scan Depth](../../use/scan-levels.md) — the depth ladder and compile-DB acquisition options.
- [What Each Level Sees](../../learn/what-each-level-sees.md) — a worked example of what L3/L4/L5 actually produce.
