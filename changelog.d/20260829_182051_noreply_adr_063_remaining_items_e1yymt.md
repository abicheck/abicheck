### Fixed

- **Two overloads distinguished only by a nested template-template
  parameter's own arity no longer collapse onto one `EntityId`.**
  `template<template<class> class TT> void f();` and
  `template<template<class, class> class TT> void f();` are legal
  overloads sharing scope, leaf name, and an identical ordinary parameter
  list; the discriminator's `TemplateTemplateParmDecl` handling recorded
  only the bare `"template"` tag, missing its own nested parameter list.
  Fixed by recursing into a template-template parameter's own children
  the same way the top-level parameter list is walked.
