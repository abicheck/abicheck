### Fixed

- **Two uninstantiated function/method templates differing only by
  template-parameter *packness* no longer collapse onto one `EntityId`.**
  `template<class T> void f();` and `template<class... T> void f();` are
  legal overloads sharing scope, leaf name, and an identical ordinary
  parameter list; the template-parameter-kind discriminator added for a
  related collision recorded only parameter *kind*
  (type/template/non-type), still reducing both to the same value. Fixed
  by tagging each discriminator entry with the node's own
  `isParameterPack` flag.
