### Fixed

- Comparing two extractions of identical headers under different directories
  no longer reports a false `internal_template_leaks_via_public_api` (and exit
  4) for lambda-parameterised templates: the checkout directory embedded in a
  `(lambda at /path/x.hpp:L:C)` spelling is now stripped from instantiation
  identity (`diff_templates`) and from clang specialization spellings,
  keeping the header basename and `line:col` so distinct lambdas stay distinct.
