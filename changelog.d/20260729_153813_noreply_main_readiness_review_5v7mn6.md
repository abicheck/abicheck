### Fixed

- **Default `dump` dependency scoping no longer lets a hidden/private
  function's own signature keep an unrelated dependency type alive.**
  `scope_snapshot_excluding_dependencies`'s direct-reference retention now
  restricts its function/variable retention roots to the public surface
  (`Visibility.PUBLIC` and not a private/system/generated origin, the
  same predicate `type_reachability.py` already uses) — previously a
  hidden helper naming a dependency type (e.g. `struct tm`) in its own
  signature kept that type retained; if the helper was later removed, the
  type silently dropped out of the next scoped snapshot too, and
  `compare` reported a spurious `TYPE_REMOVED` for a type whose real
  public-surface relevance never changed. A public declaration's own
  removal still retires the dependency type as before — that reflects a
  genuine end to the public surface's dependency on it.

