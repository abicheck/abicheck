# Case 176: kABI Export Namespace Changed

**Category:** Linux Kernel Module ABI | **Verdict:** BREAKING

## What this case is about

```
v1.symvers:
0x4e2f9a10	drm_mode_object_add	drivers/gpu/drm/drm	EXPORT_SYMBOL

v2.symvers:
0x4e2f9a10	drm_mode_object_add	drivers/gpu/drm/drm	EXPORT_SYMBOL_NS	DRM
```

`drm_mode_object_add` keeps the **same CRC** — its type signature is
untouched — and the same symbol/module. The only change: it moved from a
plain `EXPORT_SYMBOL()` to `EXPORT_SYMBOL_NS(..., DRM)`, gaining an export
*namespace*.

## Why this case matters: a separate load-gate from the CRC

`case175_kabi_crc_changed` shows a CRC break: the symbol's type signature
changed. This case is deliberately kept separate because the *mechanism*
and the *fix* are different. An export namespace is a Linux kernel access
control, independent of the CRC:

- **CRC break** — the module's compiled expectations disagree with the
  running kernel's actual layout. Fix: rebuild against the new headers.
- **Namespace break** — the module's source never declared
  `MODULE_IMPORT_NS(DRM)`. Fix: add the import declaration and rebuild —
  even though nothing about the symbol's *type* changed at all.

A module built against v1 that imports `drm_mode_object_add` normally will
fail to load against a v2 kernel with:

```
drm_mode_object_add: exists, but namespace DRM does not match the module's
imported namespaces: (empty)
```

— a load-time rejection that a CRC-only kABI check would not explain (the
CRC never changed), and that a plain symbol-presence check would miss
entirely (the symbol never disappeared).

## What abicheck detects

- **`kabi_symbol_namespace_changed`** — `drm_mode_object_add` gained an
  export namespace (`(none)` → `DRM`) while its CRC, module, and GPL class
  stayed the same. **Evidence tier L0** — read directly from
  `Module.symvers`; only a *gained or changed* namespace is flagged (a
  namespace being *removed* only widens who can import the symbol, so it is
  not a break).

**Overall verdict: BREAKING**

## How to reproduce

```bash
python3 -m abicheck.cli compare v1.symvers v2.symvers
# → BREAKING: kabi_symbol_namespace_changed (drm_mode_object_add: (none) -> DRM)
```

## Mitigation

- When introducing `EXPORT_SYMBOL_NS()` on a previously plain-exported
  symbol, document it in the release notes for out-of-tree module
  maintainers — `MODULE_IMPORT_NS()` is a source change even when no
  function signature moved.
- Prefer keeping newly-namespaced exports also available un-namespaced for
  one deprecation cycle if wide out-of-tree consumption is expected.

## References

- [Linux kernel: Symbol Namespaces](https://docs.kernel.org/core-api/symbol-namespaces.html)
- Related case: [case175_kabi_crc_changed](../case175_kabi_crc_changed/README.md)
