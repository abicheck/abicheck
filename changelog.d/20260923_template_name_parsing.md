### Fixed

- `internal_template_leaks_via_public_api` no longer skips templates whose
  instantiations take a lambda, unnamed-type or function-type argument: a `(`
  inside template arguments was mistaken for the parameter list. Operator
  names (`operator<<`, `operator->`, `operator<=>`) are no longer read as
  template brackets, so distinct operators of one template stay distinct.
