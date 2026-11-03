### Fixed

- **A parameter change of a never-exported declaration is an API break, not
  an ABI break** — when both library builds confirm a public declaration has
  no exported symbol (an uninstantiated function template, a header-only
  inline), a changed parameter list is now the new
  `header_only_function_params_changed` (API_BREAK) instead of
  `func_params_changed` (BREAKING): the library binary cannot break, but
  consumer source naming the exact instantiation type can (case122). An
  exported or unknown-export declaration keeps `func_params_changed`. The L4
  clang replay now records each function template's parameter types
  (template parameters positionally normalized) and reports the same kind
  for a changed parameter list; a body-only edit stays `template_body_changed`
  (RISK). case122's reference label moves from COMPATIBLE_WITH_RISK to
  API_BREAK, backed by a new consumer witness (`consumer_address.cpp`).
