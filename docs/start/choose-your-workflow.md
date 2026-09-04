---
doc_type: hub
audience:
  - library-maintainer
  - ci-owner
level: beginner
summarizes:
  - evidence-model
lifecycle: active
generated: false
---

# Choose Your Workflow

This is the **decision guide**. It answers a single question:

> *"I have **this** artifact, **this** configuration, and **this** problem —
> what command and options should I run?"*

The reference pages (linked throughout) explain every flag in depth. This page
is the front door: find the row that matches your situation, run the **minimum
command**, and reach for the **stronger / production command** when you need
more confidence or a CI gate.

If you only read one thing: **`abicheck compare old new` is the default
workflow.** Everything else on this page is a refinement of it for a specific
artifact layout, accuracy target, or CI policy.

---

## 1) The workflow chooser — what are you comparing?

Pick the row that matches what you physically have on disk and what you want to
know. Run the **minimum command** first; reach for the **stronger / production
command** when you need more confidence or a CI gate.

| Your situation | Minimum command | Stronger / production command |
|---|---|---|
| One shared library — does v2 break v1 consumers? | `abicheck compare libv1.so libv2.so` | `abicheck compare libv1.so libv2.so --header old=include/v1/ --header new=include/v2/` — the primary flow |
| Same public header for both versions | `abicheck compare libv1.so libv2.so -H include/foo.h` (`-H include/` scans a directory recursively) | When compiler flags affect the ABI, capture build context at dump time (`abicheck dump … -H include/foo.h -p build/`) and compare the snapshots |
| No headers at all | `abicheck compare libv1.so libv2.so` | Binary-only fallback is weaker (see [the input-quality ladder](#2-how-much-accuracy-do-you-need)); add debug info via `--debug-root old=old-debug --debug-root new=new-debug` |
| Stripped production binaries | `abicheck compare old.so new.so --debug-root old=old-debug --debug-root new=new-debug` (or `--debuginfod` to fetch by build-id) | Also pass public headers (`-H`) for highest confidence |
| A CI baseline vs a fresh build | `abicheck dump libfoo.so -H include/ -o baseline.json`, then `abicheck compare baseline.json build/libfoo.so --header new=include/` | Store baselines in GitHub Releases, the repo, the Actions cache, or artifact storage — see [Storing Baselines](../use/baseline-storage.md) |
| A PR with source/build context (catch source-only & build-flag breaks) | `abicheck scan build/libfoo.so -H include/ --sources . --against baseline.json --since origin/main` | One orchestrator over dump/compare: always-on pattern + cross-source checks plus the pinned L3/L4/L5 level — see [Source & Build Data](../learn/build-source-data.md) and the [GitHub Action: Source Scans](../use/github-action-source-scans.md) |
| Build emits source facts in parallel (combine into one baseline) | `abicheck compare old.so new.so --build-info old=abicheck_inputs/v1 --build-info new=abicheck_inputs/v2` (also auto-detects an `abicheck_inputs/` pack alongside each input with no flag at all) | No standalone merge step — `dump`/`compare` auto-ingest each side's embedded or out-of-band build/source pack directly |
| Two snapshots (offline / air-gapped) | `abicheck compare old.json new.json` | No headers/castxml/network needed — everything is baked into the snapshots |
| Several DSOs shipped together | `abicheck compare release-1.0/ release-2.0/ -H include/` (per-library results on all platforms; the cross-library bundle/dependency-skew analysis is **Linux/ELF only**) | Add `--instantiation-manifest` only for template instantiations, dlsym/plugin contracts, internal stable exports, or symbol-version promises |
| RPM / Deb / tar / conda / wheel packages | `abicheck compare old.rpm new.rpm` | Add `--debug-info old=old-debuginfo.rpm --debug-info new=new-debuginfo.rpm` (debuginfo packages) and `--devel-pkg old=old-devel.rpm --devel-pkg new=new-devel.rpm` (header/devel packages) where available |
| An application + a library upgrade | `abicheck compare libfoo.so.1 libfoo.so.2 --used-by ./myapp` | Add `-H include/`; repeatable for several application binaries; OLD/NEW may be real library binaries or JSON snapshots carrying binary evidence |
| A host that `dlopen`s plugins | `abicheck compare plugin.v1.so plugin.v2.so --required-symbol plugin_init` | Use `--required-symbols host.syms --policy plugin_abi` for a whole host-contract file |
| Will this binary load in this sysroot / rootfs? | `abicheck deps tree ./app --sysroot /rootfs` | `abicheck deps tree ./app` alone checks the dependency tree resolves |
| Two sysroots / container images to compare | `abicheck deps compare usr/bin/app --old-root /old-root --new-root /new-root` | Per-library ABI diff across the whole transitive dependency stack |
| Only a static `.a` / `.lib` archive | *(unsupported directly)* | Extract members (`ar x libfoo.a`) and compare the `.o` objects, or compare a shared library built from the same sources — see [Limitations](../learn/limitations.md#static-import-library-archives-a-lib) |

`compare` auto-detects each input: `.so` files are dumped on the fly, `.json`
snapshots are loaded directly — you can mix them freely. Deeper references:
[CLI Usage](../use/cli-usage.md), [Tool Modes](../use/tool-modes.md),
[Multi-Binary Releases](../use/multi-binary.md),
[Application Compatibility](../use/appcompat.md), [Plugin Systems](../use/plugin-systems.md).

The rest of this page covers the other three decisions, in the order you'll
meet them: **how much accuracy** you need (§2), **how CI should behave** (§3),
and **which report** to produce (§4).

---

## 2) How much accuracy do you need?

The single biggest lever on what abicheck can *prove* is the quality of the
inputs you give it — its five additive evidence layers, **L0–L4**. More
evidence catches more breaks. Start at the layer your artifacts allow, and add
more when you need more confidence. (The `scan` docs also use a sixth code,
**`L5`** — the source graph abicheck *derives* from L3/L4; you never provide it.
See [Evidence & Detectability](../learn/evidence-and-detectability.md).) For a
concrete, side-by-side look at *what each layer actually sees* on one example —
and where each one goes blind — see the
[level-by-level walk-through](../learn/what-each-level-sees.md).

| Layer | Inputs | Confidence | What it newly catches |
|:--:|---|---|---|
| **L0** | Binaries only | **Low** | Symbol add/remove, SONAME/version changes, basic metadata |
| **L1** | + debug info | **Medium** | Struct layout, field offsets, enum values, calling convention, emitted-ABI type changes |
| **L2** | + headers | **High** | Declared public API surface, source-level API breaks, inline/template-related surface |
| **L3** | + build flags (`-p build/`) | **Higher** | The exact ABI-affecting flags the library was built with (`-std`, `_GLIBCXX_USE_CXX11_ABI`, `-fvisibility`, …) |
| **L4** | + sources (build/source pack) | **Best** | Facts that never reach the binary: macro/`constexpr` values, default-argument values, uninstantiated templates |

abicheck reports the **artifact** depth it reached (L0–L2) as the
**`evidence_tier`** field (`elf_only` → `dwarf_aware` → `header_aware`) so you
can calibrate trust in any given run; build/source evidence (L3/L4) is reported
separately in the evidence-coverage table rather than promoting this scalar. See
[Output Formats → Analysis confidence and evidence
tier](../use/output-formats.md#analysis-confidence-and-evidence-tier), the per-layer
[Tool Modes](../use/tool-modes.md#abicheck-native-modes-by-evidence-source-l0l4)
reference, and [Evidence &
Detectability](../learn/evidence-and-detectability.md) for the full
explanation of why each source changes what abicheck can prove.

See [Evidence, Build-Context, and Debug Flags](../use/dump-compare-flags.md)
for how to point abicheck at debug files, build context, and compiler flags
when the default doesn't reach the layer you need.

---

## 3) How should CI behave?

abicheck separates two independent questions: **what fails the build**
(verdict/severity/exit code — `--severity-*` flags or GitHub Action
`fail-on-*`/`severity-*` inputs) and **what appears in the report**
(display-only `--show-only`, which never changes the verdict or exit code).
See [Severity Configuration](../use/severity.md) for the full failure-policy
recipe table and [Output Formats → `--show-only` filter](../use/output-formats.md#-show-only-filter)
for display filtering.

Beyond severity, two more mechanisms can each independently decide what
fails your build:

| Situation | Where to go |
|---|---|
| I want to gate only the *declared* public/export surface, not every detected change | [Contract Evaluation](../use/contract-evaluation.md) |
| I want to prove why a specific application is affected, not just that it is | `compare --used-by` + [consumer proof paths](../use/appcompat.md#why-does-this-consumer-depend-on-the-changed-declaration) |

The rest of the situations below are workflow/reporting choices, not
independent failure-policy axes — they don't add a new way to fail the
build, but they change *what you run* or *how you read the result*:

| Situation | Where to go |
|---|---|
| One library supports GCC, Clang, and MSVC and I want them checked together | [Scenario S17: Multiple Build and Compiler Profiles](../integration/scenarios/multi-platform.md) |
| I want to know whether a break is universal or profile-specific (reporting only — `finding_matrix` never changes the exit code) | [Aggregate Reports](../use/aggregate-reports.md) |
| I want the same checks runnable from Python or an agent, not just the CLI | [Typed request API](../use/python-api.md#typed-request-api) |
| I have several targets, profiles, and baseline channels to keep straight | [`project plan`](../reference/project-targets-schema.md) + [Reusable Workflows](../reference/reusable-workflows.md) + [Aggregate Reports](../use/aggregate-reports.md) |

---

## 4) Which report?

`--format markdown` (default) for PR/terminal review, `html` for a standalone
shareable report, `json` for CI logic/agents, `sarif` for GitHub Code
Scanning, `junit` for CI test dashboards. Full reference, including the
narrower bundle/package-compare format set:
[Output Formats](../use/output-formats.md).

---

## 5) CI recipes and cadence

For GitHub Actions patterns (caching a baseline, matrices, SARIF upload,
package comparisons) see [GitHub Action: More Recipes](../use/github-action-recipes.md);
for a raw-shell/other-CI pattern see [CLI Usage](../use/cli-usage.md) and
[Baseline Management](../use/baseline-management.md).

*How often* to run which depth (PR gate vs. nightly vs. release-amortized) is
covered by [Source-Scan Depth → Worked examples](../use/scan-levels.md#worked-examples)
and its [Cost guide](../use/scan-levels.md#cost-guide-rules-of-thumb) — the
L4/L5 cost cliff means "always run the deepest check on every push" is rarely
the right default.

---

## Next steps by persona

- **Library maintainer** → [Getting Started](getting-started.md),
  [Verdicts](../learn/verdicts.md),
  [Policy Profiles](../use/policies.md)
- **App developer** → [Application Compatibility](../use/appcompat.md)
- **SDK / package maintainer** → [Multi-Binary Releases](../use/multi-binary.md),
  [Baseline Management](../use/baseline-management.md)
- **CI owner** → [GitHub Action](../use/github-action.md),
  [Severity Configuration](../use/severity.md), [Output Formats](../use/output-formats.md)
- **Plugin author** → [Plugin Systems](../use/plugin-systems.md)
- **Distro / package maintainer** → [Multi-Binary Releases](../use/multi-binary.md),
  package mode in the [GitHub Action](../use/github-action.md)
- **Migrating** → [from ABICC](../use/from-abicc.md),
  [from libabigail](../use/from-libabigail.md)
