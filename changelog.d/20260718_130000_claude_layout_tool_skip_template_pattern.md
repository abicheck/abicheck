<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- `clang_layout_tool.apply_layout_facts` could attach an arbitrary
  instantiation's layout to a class template's own pattern record.
  Clang's `getQualifiedNameAsString()` spells a template pattern and every
  one of its concrete instantiations identically (no template arguments in
  the name), so a header with both the pattern and an explicitly
  instantiated `template class Box<int>;` would let the tool's
  per-instantiation `sizeof`/offsets attach to `RecordType.
  is_template_pattern` — a record with no single fixed layout by
  definition, producing false size/offset/layout-descriptor diffs for an
  unchanged template declaration. Now skips `is_template_pattern` records
  entirely, mirroring the identical guard `dumper_layout_backfill.py`'s own
  DWARF-based layout backfill already has for this exact ambiguity.
