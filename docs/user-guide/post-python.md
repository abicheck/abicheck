# POST Python ABI Commitments

[POST Python](https://post-py.org/) compiles a typed subset of Python to a
shared library. That library's **stable C ABI** is a set of `pp_*` export
symbols — the wrappers external code links against — described in a **versioned
JSON export manifest** emitted at build time (`post-py build --emit-manifest`).
Everything else in the library (the `__pp_*` compute kernels, helper symbols) is
private implementation detail that is free to churn.

`abicheck` treats that manifest as the ABI contract and gives you two things:

| You want to… | Use |
|--------------|-----|
| Compare two builds but only flag changes to the **committed** surface | `abicheck compare … --post-manifest manifest.json` |
| Check the manifest itself — is it consistent with the binary? did it break vs the previous version? | the `abicheck.post_manifest` Python API (below) |

---

## The manifest

A POST manifest is a JSON document. The top level requires `post_abi` (an
integer that must increase on any ABI-breaking release) and `exports` (the
committed symbols); each export must declare its signature — `params` and
`return_dtype` (a no-arg/void export spells `"params": []` / `"return_dtype":
""`). `c_symbol` defaults to `pp_<name>` when omitted, so an export needs at
least a `name` or an explicit `c_symbol`:

```json
{
  "post_abi": 1,
  "exports": [
    {
      "name": "gammaln",
      "c_symbol": "pp_gammaln",
      "params": ["Float64"],
      "return_dtype": "Float64",
      "ufunc": { "loop_symbol": "pp_gammaln_ufunc_loop", "signature": "()->()" }
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `post_abi` | Contract version. Must be a genuine integer; must bump on a breaking change. |
| `exports[]` | One entry per committed export (must be present — a genuinely empty surface is `"exports": []`). |
| `c_symbol` | The committed `pp_*` C symbol external code links against. Defaults to `pp_<name>` if omitted (the POST convention); a manifest whose real symbol was renamed independently of `name` is caught by manifest↔binary validation, not the manifest-only gate. |
| `params` / `return_dtype` | **Required** dtypes forming the export's signature. A param is a dtype string (`"Float64"`) or an object with a string `dtype` (`{"dtype": "Float64", "is_array": true}`); `return_dtype` is a string (`""` = void). |
| `ufunc.loop_symbol` | For vectorized exports, the committed NumPy-ufunc loop symbol (also part of the surface). |

Unknown fields are tolerated (the spec is a draft); malformed ones — a missing
`post_abi`/`exports`, a non-integer version, a duplicate `c_symbol`, a parameter
object with no `dtype` — are rejected so a bad manifest cannot quietly hide a
change.

---

## `compare --post-manifest` — scope a diff to the committed surface

When you compare two POST builds, most of the diff is private-kernel churn you
do **not** care about. Pass the manifest and `abicheck` scopes the verdict to
the committed `pp_*`/ufunc-loop symbols only:

```bash
abicheck compare libmylib.v1.so libmylib.v2.so --post-manifest manifest.json
```

- A change to a **committed** symbol (`pp_gammaln` signature change, removal, a
  dropped/renamed ufunc loop) drives the verdict as usual.
- A change to anything **not** committed — a private `__pp_*` kernel, an internal
  helper, an added non-committed export — is moved to the *filtered ledger* and
  does not affect the verdict.
- **Nothing that could hide a real break is filtered.** Type-layout changes,
  internal-leak findings, and loader-contract changes (`SONAME`, `DT_NEEDED`)
  are always kept — a struct passed to a committed export or a changed SONAME
  breaks clients regardless of the export set.

See exactly what was demoted with `--show-filtered` (text) or under the
`surface_scope` key (`--format json`):

```bash
abicheck compare libmylib.v1.so libmylib.v2.so \
    --post-manifest manifest.json --show-filtered
```

The manifest surface is authoritative, so this works independently of
`--scope-public-headers`; the filtered ledger is always reported so a clean
verdict never hides that filtering happened.

!!! note "Removed symbols and the `pp_*` namespace"
    When you point `--post-manifest` at the **new** manifest, a committed
    wrapper that was *removed* in the release is no longer listed there. Binary
    scoping recovers such removals by the `pp_*` committed namespace so the
    removal still breaks — but a committed ufunc `loop_symbol` that is *not*
    `pp_`-prefixed may fall outside this recovery. The manifest-to-manifest
    checks below (**diff** / **gate**) see both versions and are the
    authoritative gate for loop-symbol renames/removals; treat
    `compare --post-manifest` as the best-effort binary-level surface filter.

**Exit codes** are the standard `compare` codes — `0` compatible, `2` source
break, `4` ABI break (or the severity-aware scheme when any `--severity-*` flag
is set) — so it drops straight into a CI gate:

```bash
# Fail the build only on a change to the committed POST surface.
abicheck compare libmylib.v1.so libmylib.v2.so --post-manifest manifest.json
```

---

## Checking the manifest itself (Python API)

The manifest-native checks are available as a small library —
`abicheck.post_manifest` — for release scripts and CI. Each returns a result
object plus a `format_*` reporter.

### 1. Validate the manifest against the built library

Confirm every promised `pp_*` and ufunc-loop symbol is actually exported
(ELF / PE-COFF / Mach-O). Run it right after `post-py build`:

```python
from pathlib import Path
from abicheck.post_manifest import validate_from_binary, format_validation_report

result = validate_from_binary(Path("manifest.json"), Path("libmylib.so"))
print(format_validation_report(result))
if not result.passed:          # a promised symbol is missing from the binary
    raise SystemExit(1)
```

### 2. Diff two manifest versions

A compiler-independent diff keyed by `c_symbol`, using the manifest's own
dtypes (which a stripped binary no longer carries). Removed exports, changed
signatures, and changed ufunc loop signatures/symbols are breaking; added
exports and added ufunc facets are compatible:

```python
from abicheck.post_manifest import load_manifest, diff_manifests, format_diff_report

diff = diff_manifests(load_manifest(Path("v1.json")), load_manifest(Path("v2.json")))
print(format_diff_report(diff, "v1", "v2"))
if diff.is_breaking:
    ...
```

### 3. Gate the `post_abi` version bump

The release check: a breaking change must be accompanied by a `post_abi`
increase.

```python
from abicheck.post_manifest import load_manifest, check_version_gate, format_gate_report

gate = check_version_gate(load_manifest(Path("v1.json")), load_manifest(Path("v2.json")))
print(format_gate_report(gate, "v1", "v2"))
if gate.violated:              # breaking change without a post_abi bump
    raise SystemExit(1)
```

---

## Typical release flow

1. `post-py build --emit-manifest` → produces `libmylib.so` + `manifest.json`.
2. **Post-build:** `validate_from_binary(...)` — did the build actually export
   everything the manifest promises?
3. **On a release PR:** `check_version_gate(old_manifest, new_manifest)` — was a
   breaking change matched by a `post_abi` bump? *(fails the PR if not)*
4. **Optional, binary-level:** `abicheck compare old.so new.so
   --post-manifest manifest.json` — diff the actual binaries, scoped to the
   committed surface, so private-kernel churn stays out of the verdict.

!!! note
    Steps 1–3 are a Python library today (no dedicated CLI subcommand yet);
    step 4 — `compare --post-manifest` — is the CLI entry point.
