---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Renaming a Rust crate changes every crate-qualified type name, so an unchanged C surface reads `API_BREAK` on the DWARF path (2026-10-15)

**Status (2026-10-15): OPEN.** Reproduced on two cdylibs with identical source and C ABI, differing only in `[package] name`.

```
abicheck compare librustlib.so librustlib2.so      # DWARF only
# verdict API_BREAK, exit 2
#   type_removed rustlib::Point
#   type_added   rustlib2::Point
abicheck compare librustlib.so librustlib2.so -H old=h -H new=h   # same header
# verdict NO_CHANGE, exit 0
```

DWARF names of Rust types are qualified by the crate root, so a repr(C) `Point` is `rustlib::Point`. The C-visible identity of `Point` is the bare name, and a header sees exactly that. A crate rename therefore reads as a removed type and an added type on the DWARF path.

Fix direction: for crate-root types that are reachable from a `#[no_mangle]` export, identity should use the unqualified name. That needs a decision on namespace semantics, since C++ namespaces are significant and must keep their qualifiers. Not attempted.

Workaround: keep the crate name stable across versions, or compare with headers. See [Rust Libraries](../../use/rust-libraries.md).

Index: [Known gaps](../known-gaps.md)
