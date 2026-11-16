<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A raised variable alignment is no longer a clean pass at headers depth.** A stripped binary with public headers read a variable's `aligned(N)`/`alignas(N)` only as the type's natural alignment, a lower bound, so `aligned(16) -> aligned(64)` reported NO_CHANGE. The clang header join now reads each declaration's own specifier and the castxml reader takes it as declared (extractor generation 77), so the change reads `VAR_ALIGNMENT_CHANGED` (BREAKING), and a dropped specifier is reported undemoted, as the clang backend reports it.
