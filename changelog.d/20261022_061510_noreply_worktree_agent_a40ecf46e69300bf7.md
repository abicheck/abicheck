<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A removed vtable, typeinfo, typeinfo-name or VTT export is judged by its owning class** -- under `--contract public` a `_ZTV`/`_ZTI`/`_ZTS`/`_ZTT` export that no header spells was left `UNKNOWN_UNRESOLVED` ("Not Evaluated", `required_evidence_incomplete`) even when the class was declared in a public header, so the removal never reached the verdict. The owning class is now recovered exactly from the mangling (`itanium_class_metadata_owner`) and confirmed by its header declaration, so such a removal is in contract and breaks the verdict; a class no header declares stays out of contract and is still reported. The typeinfo name (`_ZTS`) now has an owner like the other three prefixes, and the finding no longer says the class is "declared by no header by construction".
