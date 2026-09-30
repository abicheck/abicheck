### Changed

- `RecordType.bases`, `virtual_bases`, `vtable` and `vptr_offset_bits`, and `Param.is_va_list`, are no longer stored fields (ADR-063 Phase 10). The matching `*_fact` field is now the only stored value. The names still work as constructor arguments and as read-only views of that fact. Assigning to one now raises `AttributeError`: build a new instance or use `replace_with_fact_sync()`. Previously such an assignment silently left the fact stale. Snapshot documents are unchanged.
