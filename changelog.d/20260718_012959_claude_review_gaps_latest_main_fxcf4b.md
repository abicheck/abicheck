### Documentation

- **Documented a known clang-frontend gap in the case61-class alignment false-positive fix.** The
  `case61_var_added` fix (natural type-alignment corroboration for `exported_object_alignment_reduced`)
  only applies to the CastXML backend: `dumper_clang.py`'s `_clang_var_alignment_bits` can only read
  an explicit `AlignedAttr` override, never a variable's natural type alignment, because clang's
  `-ast-dump=json` does not compute layout at all (no compiler-derived value exists to fall back to,
  unlike CastXML's real compiler output). Cross-referenced the gap from `dumper_clang.py`'s module
  docstring, `_clang_var_alignment_bits`, and `_check_object_alignment_reduced`, and added a live
  regression test (`tests/test_clang_header_backend_integration.py`) that locks in the current,
  honest behavior so a future fix attempt updates a real assertion instead of silently going
  unnoticed.
