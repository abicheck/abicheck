# Case 113: ABI-tag set change ([abi:cxx11] lost on a single symbol)

**Category:** Binary ABI break / C++ mangling | **Verdict:** 🔴 BREAKING

## What changed

In `v1`, `get_id()` carries an explicit Itanium ABI tag via
`[[gnu::abi_tag("cxx11")]]`, so its mangled symbol is `_Z6get_idB5cxx11v`
(the `B5cxx11` component encodes the tag). In `v2` the tag is removed, so the
symbol becomes `_Z6get_idv`. The *demangled* declaration is identical, but the
two mangled names are different symbols.

Unlike the libstdc++ dual-ABI mass flip (which churns hundreds of symbols at
once and is reported once as `glibcxx_dual_abi_flip_detected`), this is a
single symbol whose ABI-tag set changed.

## How abicheck catches it

`abi_tag_changed` fires because the function's ABI-tag set differs while the
base (tag-stripped) mangled name matches. A consumer linked against the v1
tagged symbol gets an undefined-symbol error at load time against v2. The
detector stays silent when a mass dual-ABI flip is detected, to avoid
duplicate noise.

## Files
- `v1.cpp` / `v2.cpp` — the two library builds, with the tagged vs untagged
  `get_id()` declaration inlined (no header: the snapshot is taken from the
  compiled library's DWARF). This case is Linux-only — the ABI-tag rename is
  only visible via the ELF/DWARF path.
- `app.cpp` — minimal consumer stub
