### Added

- **`overload_ambiguity_introduced`** — a new overload (constructors included) that joins an existing one with the same arity and differs only at positions where both take a scalar is now reported with the concrete call it makes ambiguous, e.g. `mylib::enumerable_thread_specific({})`. It is a consumer-conditional source break, so its default verdict is `COMPATIBLE_WITH_RISK`; catalog case111 is detected instead of reading `COMPATIBLE`.
