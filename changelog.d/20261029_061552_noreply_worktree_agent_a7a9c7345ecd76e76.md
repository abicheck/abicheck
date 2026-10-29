<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **A base-kind suppression no longer depends on whether headers were supplied** — a removal or addition found from the ELF export table alone is `func_removed_elf_only` (or `func_added_elf_only`, `var_removed_elf_only`, `var_added_elf_only`) instead of the base kind, so a `change_kind: func_removed` suppression, reclassification or acknowledgment used to stop matching a headerless run. A rule naming the base kind now matches both kinds; a rule naming the `*_elf_only` kind still matches only that kind. The pairs come from the change catalog (`model/change_catalog/kind_variants.py`), and a hidden finding records the match as `matched_via_kind` in its disposition's rule provenance.
