# Policy Profiles

`abicheck compare` supports policy-based verdict classification.

- Built-in profiles: `--policy strict_abi|sdk_vendor|plugin_abi`
- Custom profile file: `--policy-file <yaml>`

`abicheck compat` intentionally does **not** expose `--policy`; it stays aligned
with ABICC-compatible behavior (`strict_abi` semantics) plus its own legacy flags
like `-strict`/`--strict-mode`.

## Usage

```bash
abicheck compare old.json new.json --policy strict_abi   # default
abicheck compare old.json new.json --policy sdk_vendor
abicheck compare old.json new.json --policy plugin_abi
abicheck compare old.json new.json --policy-file policy.yaml
```

## Available Profiles

### `strict_abi` (default)

Full strictness — every detected ABI change is classified at its maximum severity.

| Verdict | Meaning |
|---------|---------|
| `BREAKING` | Binary ABI break — old callers will crash or misbehave |
| `API_BREAK` | Source-level break — recompile required, but binary may still work |
| `COMPATIBLE_WITH_RISK` | Binary-compatible, but deployment risk present — verify target environments |
| `COMPATIBLE` | Safe addition or informational |
| `NO_CHANGE` | No differences found |

The `soname_bump_recommended` advisory is emitted as COMPATIBLE (quality issue) when
binary-incompatible changes are detected but the SONAME is not bumped. The underlying
breaking changes themselves carry the BREAKING verdict. Use custom policy files to
escalate `soname_bump_recommended` to `break` if you want SONAME-bump enforcement to
fail CI:

```yaml
overrides:
  soname_bump_recommended: break
```

Use for: shared libraries, system libraries, public SDKs with strict compatibility guarantees.

---

### `sdk_vendor`

Permissive profile for SDK / vendor libraries. Source-level-only changes
(renames, access changes) are downgraded from `API_BREAK` to `COMPATIBLE`
since SDK consumers typically use stable binary interfaces, not source-level names.

**Downgraded to `COMPATIBLE` under `sdk_vendor`:**

| Change Kind | Description |
|-------------|-------------|
| `enum_member_renamed` | Enum member name changed (value unchanged) |
| `field_renamed` | Struct/class field name changed |
| `param_renamed` | Function parameter name changed |
| `method_access_changed` | Method access level changed |
| `field_access_changed` | Field access level changed |
| `source_level_kind_changed` | `struct` ↔ `class` keyword (binary-identical) |
| `removed_const_overload` | const overload removed |
| `param_default_value_removed` | Default argument removed |

All `BREAKING` kinds remain `BREAKING` — this profile does not suppress binary breaks.

Use for: vendor SDKs, optional library extensions, plugin APIs where source compat is not required.

---

### `plugin_abi`

Relaxed profile for plugins that are built from the **same toolchain** as the host
at the same time. Calling-convention signals are downgraded to `COMPATIBLE` since
they are controlled by the build system rather than the library ABI contract.

**Downgraded to `COMPATIBLE` under `plugin_abi`:**

| Change Kind | Description |
|-------------|-------------|
| `calling_convention_changed` | DWARF DW_AT_calling_convention drift |
| `frame_register_changed` | CFA/frame-pointer register changed (.eh_frame) |
| `value_abi_trait_changed` | DWARF triviality heuristic (pass-by-reg vs pointer) |

All `BREAKING` kinds that are not calling-convention-related remain `BREAKING`.

`toolchain_flag_drift` is already `COMPATIBLE` in the default policy (informational),
so it is not part of the plugin downgrade set.

Use for: dynamically-loaded plugins, JNI/Python extension modules, hot-reload scenarios
where the plugin and host are always rebuilt together.

---

## Built-in use-case profiles

Beyond the three base policies, abicheck ships a catalog of **turnkey, ecosystem-specific
profiles** as YAML files under `abicheck/policies/`. A bare name resolves to the shipped
file, so they need no path:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 --policy-file qt_kde_cpp
```

Each profile builds on `strict_abi` and adjusts only where the ecosystem's documented
compatibility rules differ from the strict default. They are derived from primary-source
guidance, not invented heuristics.

| Profile | Ecosystem / source | What it changes vs `strict_abi` |
|---------|--------------------|---------------------------------|
| `security` | checksec-style hardening | Promotes RELRO/PIE/canary/FORTIFY/NX regressions to `break` |
| `qt_kde_cpp` | [KDE C++ Binary Compatibility rules](https://community.kde.org/Policies/Binary_Compatibility_Issues_With_C%2B%2B) (Qt points here too) | Promotes `func_noexcept_removed` to `break`; documents the virtual/layout/enum rules strict already enforces |
| `glibc_symbol_versioned` | glibc symbol-versioning discipline | Pins version-node removals to `break`, accepts compat-version requirement additions, flags dropped `DT_NEEDED` as `risk` |
| `msvc_pe` | [MSVC C++ binary compatibility](https://learn.microsoft.com/cpp/porting/binary-compat-2015-2017) + x64 ABI | Pins `calling_convention_changed` to `break`; dropped import DLL → `risk` (no RPATH fallback on Windows) |
| `mach_o_dylib` | Apple Dynamic Library Design Guidelines | Pins `compat_version_changed` to `break` (dyld load check); dropped install-name dependency → `risk` |
| `rust_c_ffi` | Rust Reference / Cargo SemVer (no stable Rust ABI) | Keeps the C-FFI surface (`repr(C)`/`extern "C"`) strict but demotes C++-object-model kinds to `risk` — they can't occur on a real C-FFI boundary |
| `gnome_parallel_install` | GNOME/GTK parallel-install evolution | Enforces both directions of SONAME discipline: pins `soname_bump_recommended` to `break` (broke ABI without bumping), and surfaces `soname_bump_unnecessary` as `risk` (bumped the major for nothing, fragmenting parallel-installed consumers) |

> **Why most profiles are thin:** the default `strict_abi` already classifies the hard
> native cases (symbol removal, layout, vtables, mangling, calling convention) correctly,
> so a profile mostly adds a named entry point, primary-source documentation, and the few
> genuine per-ecosystem divergences. Managed-runtime ecosystems (Java class-file linkage,
> .NET assembly metadata) and source-only ecosystems (Go, non-FFI Rust) need dedicated
> format frontends rather than a policy file — see
> [ADR-034](../development/adr/034-managed-runtime-and-non-c-abi-frontends.md).

## Custom Policy Files (`--policy-file`)

Custom policy files let you keep all detectors enabled and only override
how specific change kinds are classified.

Minimal example:

```yaml
base_policy: strict_abi   # optional, default strict_abi
overrides:
  enum_member_renamed: ignore   # break|warn|ignore
  field_renamed: ignore
  calling_convention_changed: warn
```

Semantics:
- `break`  → `BREAKING` (exit code 4)
- `warn`   → `API_BREAK` (exit code 2)
- `risk`   → `COMPATIBLE_WITH_RISK` (exit code 0; deployment risk visible in output)
- `ignore` → `COMPATIBLE` (exit code 0)
- kinds not listed in `overrides` use `base_policy`

If both `--policy` and `--policy-file` are provided, `--policy-file` wins.

### Evidence-aware controls (`evidence_policy`)

When a compare also carries build/source evidence (build-info / source packs,
ADR-028..033), an optional `evidence_policy` block tunes how each *category* of
evidence finding is classified — independent of the per-`ChangeKind` `overrides`
above:

```yaml
evidence_policy:
  source_only_findings: warn          # ignore | warn | fail-api | fail-release
  build_context_drift: warn           # ignore | warn | fail-on-abi-relevant
  graph_risk_findings: warn           # ignore | warn | fail
  require_evidence:                    # fail if a required layer is not comparable
    build_context: false
    source_abi: false
    graph_summary: false
```

- `source_only_findings` — L4 source-replay / API-only findings (macros, default
  args, inline/template/constexpr bodies). `ignore` → `COMPATIBLE`, `warn` →
  `COMPATIBLE_WITH_RISK`, `fail-api`/`fail-release` → `API_BREAK` (exit 2).
- `build_context_drift` — L3 build-flag / toolchain drift. `fail-on-abi-relevant`
  escalates only genuinely ABI-relevant drift (std/visibility/packing flags,
  export policy, toolchain) to `API_BREAK`; other drift stays a risk.
- `graph_risk_findings` — L5 reachability/impact risks. `fail` → `API_BREAK`.
- `require_evidence` — when a listed layer is `true` but absent from either the
  baseline or target side of the compare, an `evidence_required_missing` finding
  (`API_BREAK`) fails the run so a silently-degraded scan can't pass (ADR-033 D7).

Each knob is **unset by default**: leaving it out keeps the finding's normal
category, so existing runs are unchanged. Per ADR-028 D3 these knobs never turn
a source/build-only finding into a hard (artifact-proven) `BREAKING` verdict —
the strongest they reach is `API_BREAK`.

---

## Exit Codes

For `abicheck compare`, exit codes are the same for all policies — only the verdict changes:

| Exit Code | Verdict |
|-----------|---------|
| `0` | `NO_CHANGE` or `COMPATIBLE` |
| `0` | `COMPATIBLE_WITH_RISK` (deployment risk, inspect output) |
| `2` | `API_BREAK` (source-level break) |
| `4` | `BREAKING` (binary ABI break) |

For `abicheck compat`, policy still affects verdict classification, but command-level
options (`-strict`, `--strict-mode`, legacy compatibility behavior) can additionally
modify the final process exit status. Treat the table above as `compare` semantics.

---

## Extending Policies

Built-in profiles are defined in `abicheck/checker_policy.py`:
- `SDK_VENDOR_COMPAT_KINDS` — kinds downgraded to COMPATIBLE under `sdk_vendor`
- `PLUGIN_ABI_DOWNGRADED_KINDS` — kinds downgraded to COMPATIBLE under `plugin_abi`

Custom file parsing/overrides live in `abicheck/policy_file.py` (`PolicyFile`).
