### Fixed

- **ADR-085 shape C matches class templates by qualified name** — a reported function's spelled template argument (`Box<char>`) is resolved from the function's own enclosing scope outward, as C++ does, so `b::take(const Box<char>&)` claims `b::Box` and never `a::Box`. Before, the short name `Box` alone let two same-named templates in different namespaces, or a nested class and a namespace-level template, suppress each other's `TEMPLATE_PARAM_DEFAULT_CHANGED` finding. A header-level default change that no reported signature names is now always reported.
