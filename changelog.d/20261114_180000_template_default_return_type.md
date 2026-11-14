### Fixed

- `default_template_arg_changed` now pairs the instantiations of a function
  template whose return type is the defaulted parameter
  (`template <class T, class U = int> U f(T)`; `f<int>` re-mangles when `U`'s
  default changes). The return type was part of the pairing key, so the two
  instantiations never met and only the header-level
  `template_param_default_changed` (or nothing, from symbols alone) was
  reported.
