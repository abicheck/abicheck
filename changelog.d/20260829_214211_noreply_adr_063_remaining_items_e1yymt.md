### Fixed

- **Two more real return-type identity collisions in the clang header
  backend.** A trailing return type that itself contains parentheses
  (`auto f(T) -> decltype((T::x))`) had those parentheses mistaken for a
  second parameter-list group, discarding the dependent operand and
  collapsing two legal overloads onto the identical return type — fixed
  by checking for a top-level trailing-return arrow before ever looking
  for a parameter-list group. Separately, a function-pointer/reference
  return type's OWN parameter list (`typename S::x (*f(T))(int)` vs.
  `...(double)`) was discarded outright instead of preserved, losing the
  one thing distinguishing two such overloads — fixed by recursively
  excising only the wrapped function's own parameter list and keeping
  the returned function type's parameter list intact. `_return_type`
  (now `abicheck/extract/headers/clang/return_type.py`'s `return_type`,
  split into its own leaf module to stay under the AI-readiness
  production-file line cap) resolves both together with every
  previously-fixed case (ordinary, spiral, dependent-parens,
  `noexcept`).
