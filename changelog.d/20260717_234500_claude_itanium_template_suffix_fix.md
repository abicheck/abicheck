<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`--ast-frontend hybrid`**: the ctor/dtor scope normalizer's Itanium
  template-argument stripper searched only the FIRST `"I"` character in a
  scope component, so a class whose base name itself contains an uppercase
  `"I"` (`Image`, `Iterator`, `MultiIndex`, ...) started the skip at the
  wrong position and never reached the end of the string — the component
  came back unstripped (`"ImageIiE"` instead of `"Image"`), permanently
  mismatching against CastXML's own normalized spelling and leaving such a
  template class's unchanged ctor/dtor as a false remove+add pair. Now
  tries every `"I"` occurrence in turn and takes the first whose
  template-argument skip exhausts the entire remaining string.
