### Fixed

- **castxml reports a header-only template-default change.** castxml writes
  no element for an uninstantiated template, so changing a default template
  argument read `NO_CHANGE` (with `partial` assurance) on castxml while clang
  reported `template_param_default_changed`. The castxml backend now reads
  every template's defaults from the clang AST of the same header, through
  the same cached join that reads `noexcept` and C++20 constraints, so both
  backends report the change alike (extractor generation 46). Without clang,
  castxml still reads `NO_CHANGE` with `partial` assurance and
  `template-parameter defaults (castxml cannot record them)` named.
- **Alias and variable template defaults are compared on both backends.**
  The clang backend had no model of alias or variable templates, so a
  default change on one only named an assurance gap; it is now reported as
  `template_param_default_changed`. Type, non-type and template-template
  defaults are covered for class, function, alias and variable templates. A
  default only added to a class, alias or variable template is not a break.
  A default the AST walk cannot spell (`sizeof(T)`), or a member template of
  an explicit specialization, reads `partial` with the gap named.
- **A default change already reported through a public signature is counted
  once on castxml too.** For example, `take(Box<char>)` reported as removed
  and added.
