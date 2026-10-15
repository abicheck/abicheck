---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
level: intermediate
lifecycle: active
generated: false
canonical_for:
  - rust-libraries
depends_on:
  - abicheck/policies/rust_c_ffi.yaml
  - abicheck/package.py
  - abicheck/diff_templates.py
---

# Rust libraries

abicheck compares a Rust library the same way it compares a C library: as a
native shared object, with its C-visible surface read from the binary and,
optionally, from headers. **Rust does not have a stable ABI.** The only part of
a Rust library that is a binary contract is the part you deliberately expose as
a C ABI: `#[unsafe(no_mangle)] pub extern "C" fn` (`#[no_mangle]` in edition 2021), `#[repr(C)]` types, and exported
statics. That is the surface abicheck checks for Rust.

This page declares what is supported today, what is verified, and what still
behaves differently. The verdicts below come from compiling small cdylibs with
`cargo` and running `abicheck compare` on pairs of them.

## Support statement

| Input | Status | What you get |
|-------|--------|--------------|
| `cdylib` (`.so`) exporting a C ABI, compared **with a C header** | **Supported** | Full verdicts for function, return-type, parameter, `#[repr(C)]` layout, exported-static, and removal changes. Works on a default `cargo build --release`. |
| `cdylib` compared **DWARF-only** (`debug = 2` in the release profile, no header) | **Supported with caveats** | Same verdicts as the header path for the C surface, plus callback signatures, which the header path cannot see. See [caveats](#caveats). |
| `cdylib` with no header and no DWARF (default release profile, no header) | **Not a usable check** | The result is `NO_CHANGE` and exits 0 even when a return type changed. Do not rely on it. See [caveats](#caveats). |
| Rust-native API (`pub fn`, generics, `repr(Rust)` types, without `extern "C"`) | **Not supported** | rustc does not export these from a cdylib, and Rust has no stable ABI for them. abicheck does not see them. |
| `rlib` and `staticlib` (`.rlib`, `.a`) | **Not supported** | Rejected with an explicit message (exit 64). Compare the shared library built from them instead. |
| Rust generic types in a C signature (`Option<extern "C" fn(...)>` is the only one tested) | **Not evaluated beyond that case** | The `Option` callback case works on the DWARF path. Other generic types have not been run. Treat their results as a review aid, not a gate. |

The policy profile `rust_c_ffi` ([Policy Profiles](policies.md)) is a classification
profile for the C surface. It keeps the C-relevant checks strict and demotes the
C++ object-model kinds that cannot occur on a `repr(C)` / `extern "C"` boundary.
It does not add a Rust analyzer. None of the cases below produced an overridden
kind, so in these experiments it did not change any verdict.

## Recommended setup

Compare the same shared object you ship. Generate the header from the Rust
source with [cbindgen](https://github.com/mozilla/cbindgen), and keep DWARF on
for the artifact you compare:

```toml
# Cargo.toml of the library you compare
[lib]
crate-type = ["cdylib"]

[profile.release]
debug = 2   # full DWARF: needed for callback signatures and for the DWARF-only path
```

```bash
cargo build --release
cbindgen --lang c --output include/mylib.h     # regenerate per version
abicheck compare \
  old/libmylib.so new/libmylib.so \
  -H old=old/include/mylib.h -H new=new/include/mylib.h \
  --policy rust_c_ffi
```

`debug = 2` adds debug information only; it does not change the code that is
generated. Keep the crate name stable across versions (see
[caveats](#caveats)).

## What was verified

Each row is one comparison of a baseline cdylib against a variant with one
change. Headers are from cbindgen unless marked otherwise.

| Change | Header, default release (no DWARF) | DWARF only (`debug = 2`) | No header, no DWARF (default release) |
|--------|-------------------------------------|---------------------------|----------------------------------------|
| Exported function added | `COMPATIBLE` | `COMPATIBLE_WITH_RISK` (see [caveats](#caveats)) | `COMPATIBLE_WITH_RISK` |
| Exported function return type changed (`i32` → `i64`) | `BREAKING` (`func_return_changed`) | `BREAKING` (`func_return_changed`) | `NO_CHANGE`, exit 0 (missed) |
| `#[repr(C)]` struct gained a field | `BREAKING` (`type_size_changed`) | `BREAKING` | `NO_CHANGE`, exit 0 (missed) |
| `#[repr(C)]` union gained a member | `BREAKING` (`type_size_changed`, `union_field_added`) | `BREAKING` | not run |
| `#[repr(C)]` enum value changed | `BREAKING` (`enum_member_value_changed`) | `BREAKING` | not run |
| `#[repr(C)]` enum member added | `COMPATIBLE` (`enum_member_added`) | `COMPATIBLE` | not run |
| Field array size changed (`[u8; 16]` → `[u8; 32]`) | `BREAKING` | `BREAKING` (size and offset; the array type change is not reported) | not run |
| Exported static retyped (`i32` → `i64`) | `BREAKING` (`var_type_changed`) | `BREAKING` | `BREAKING` (`symbol_size_changed` only) |
| Exported function removed | `BREAKING` (`func_removed`) | `BREAKING` | `BREAKING` (`func_removed_elf_only`) |
| Callback parameter arity changed (`extern "C" fn(i32)` → `fn(i32, i32)`) | `NO_CHANGE` (missed) | `BREAKING` (`func_params_changed`) | not run |
| `pub fn` (not exported) changed signature | `NO_CHANGE` | `NO_CHANGE` with a crate name held fixed | `NO_CHANGE` |
| `.rlib` / `.a` input | rejected, exit 64 | rejected, exit 64 | rejected, exit 64 |

The header rows use the header the variant's own cbindgen run produced. The
"Callback parameter arity" row is the one place the header path is weaker: see
[caveats](#caveats).

## Caveats

These are the behaviors a Rust maintainer should know. Each has a tracked entry
in [Known gaps](../contribute/known-gaps.md).

- **A release build without debug info is not a check on its own.** With the
  Cargo default (`debug = 0`), abicheck has no type information unless you pass
  headers. Without headers, a real return-type break reads `NO_CHANGE`, and the
  report says `analysis_assurance: partial`. Always pass cbindgen headers, or
  build with `debug = 2`.
- **`debug = 1` is not enough.** `debug = 1` emits DWARF but leaves
  out the function types. The return-type break above was missed with
  `analysis_assurance: complete`. Use `debug = 2`.
- **Callback signatures are invisible to the header path.** A typedef'd
  `extern "C"` function pointer changes arity under the same name; abicheck
  reads the parameter as "a pointer" and reports no change. The DWARF path sees
  it. This is not Rust-specific; C function pointers behave the same way.
- **Renaming the crate changes every type's identity.** DWARF names are
  crate-qualified (`mylib::Point`). Renaming the crate with an unchanged C
  layout reports `type_removed` and `type_added`, which gives `API_BREAK` on the
  DWARF path. The header path reports `NO_CHANGE`. Keep the crate name stable,
  or compare with headers.
- **Header-less additive changes can carry a risk finding.** Without headers,
  an added exported function can be followed by `exported_object_alignment_reduced`
  on an unrelated exported static. That alignment is derived from the symbol's
  link-time address, not its declared alignment. With headers the finding does
  not appear.
- **Rust generics leave `Option<...>`-style pairs.** A type such as
  `Option<extern "C" fn(i32) -> i32>` embeds its signature in its name, so a
  signature change shows as a `type_removed` / `type_added` pair next to the real
  `func_params_changed`. The arity finding that used to accompany this was a bug
  and is fixed.

## Fixed for Rust

Two problems surfaced during this check and were fixed in the same change:

- A static archive (`.a`, `.rlib`) was sniffed as a Debian package because both
  start with the `!<arch>` magic, and failed with "No data.tar.* found in Deb
  package". A `.deb` is now recognised by its first member, `debian-binary`, so
  archives get the explicit "static/import library archive" rejection.
- A function-typed template argument (`Option<extern "C" fn(i32, i32) -> i32>`)
  was counted as having two template arguments, because the commas of its
  parameter list and the `>` of its `->` return arrow were read as template
  syntax. That produced a false `mandatory_template_param_added`. The counter
  now ignores both.

## Tests

- `tests/test_rust_cdylib_integration.py` (marker `integration`, skipped without
  `cargo`) builds real cdylibs and checks the header path, the DWARF path, the
  callback arity case, and the non-exported Rust-native case.
- `tests/test_deb_extractor_detection.py` and
  `tests/test_diff_templates_fn_type_arity.py` cover the two fixes without a
  toolchain.

## See also

- [Policy Profiles](policies.md): the `rust_c_ffi` profile and the other built-ins.
- [Platform Support](../reference/platforms.md): what is analysed on which host.
- [Known gaps](../contribute/known-gaps.md): the open entries behind the caveats.
