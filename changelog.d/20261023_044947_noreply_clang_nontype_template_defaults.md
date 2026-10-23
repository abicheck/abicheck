<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **Clang records non-type and template-template template-parameter defaults** —
  the clang header backend now spells the default of a `NonTypeTemplateParmDecl`
  (`int N = 4`, `Mode M = Mode::B`) and of a `TemplateTemplateParmDecl`
  (`template <typename> class C = Box2`) as well as a type parameter's, so a
  header-only change to any of them is reported as `TEMPLATE_PARAM_DEFAULT_CHANGED`
  (`API_BREAK`) instead of reading `NO_CHANGE`. A default whose spelling is not
  read (for example `sizeof(T)`) still leaves its pattern unrecorded, and the
  assurance gap says so. No schema change: the fact is the same `v62` value, so
  a pre-existing clang baseline with a non-type template reads as not recorded
  until it is re-dumped. castxml behaviour is unchanged.
