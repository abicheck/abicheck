<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Lowered the CastXML version-gate floor from `>=0.7.0` to `>=0.6.11`.**
  The floor was calibrated to conda-forge's `castxml` feedstock numbering,
  but the CastXML Superbuild (what `action/install-castxml.sh` pins for CI,
  and what most real-world consumers of this project's GitHub Action
  actually run) tracks its own, much slower-moving internal version
  number — its latest actively-maintained release is still numbered 0.6.x
  despite bundling a current LLVM/Clang (21.x). A floor copied from
  conda-forge's scale rejected every real Superbuild install indefinitely,
  including this repo's own pinned CI build, forcing an automatic fallback
  to the clang header backend that misses real ABI breaks the castxml
  frontend catches (e.g. vtable reorders). `MIN_CASTXML` now tracks the
  Superbuild's `v0.6.11` generation (Feb 2024, the first tag in its
  current release line) — still well above the legacy PyPI `castxml`
  distribution (`0.4.5`) the gate is meant to reject, and
  `MIN_CASTXML_CLANG_MAJOR` (unchanged) remains the primary technical
  quality signal.
</content>
