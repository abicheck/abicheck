### Changed

- Idiom recognition (`abicheck/idioms.py`) no longer repeats work it can prove
  is redundant. `OPAQUE_POINTER`'s two record-local conditions (the definition
  must be incomplete, and no field may be `PUBLIC`) are now evaluated *before*
  the public-signature search rather than after it, so a complete record or one
  with public fields never triggers that search; both are pre-existing
  necessary conditions, so every tag, its evidence and its proof fields are
  unchanged. The pointer/cv-token strip is now memoised through a new
  dependency-free leaf, `abicheck/policy/type_spelling.py`, whose cache is
  bounded (4096 entries, spellings over 512 characters bypass it and are still
  normalised in full) and stores only plain strings, so it cannot extend the
  lifetime of any graph or snapshot. The existing *sequential* keyword
  substitutions are preserved exactly -- they are deliberately not folded into
  one combined alternation, which would change the result for valid spellings
  such as `Foo*const`.
- `OPAQUE_POINTER`'s public-signature search is now answered from one inverted
  index over public signature sites (`abicheck/policy/public_use_index.py`)
  instead of re-walking every public function for each eligible record. The
  index is a mechanical inversion of the existing predicate -- same
  public-surface admission test, same short-name and qualified matching clauses
  kept separate, same by-value test, and any one by-value use still defeats
  "only pointer" -- so it introduces no type resolver and no ambiguity policy.
  It is built at most once per recognition, never when no record is eligible,
  holds only strings and bools, and is local to the call rather than cached
  against a graph or attached to a snapshot.
- The normalisation memo is an explicit bounded LRU rather than
  `functools.lru_cache`, so its retained set can be read back
  (`strip_ptr_cache_entries`) and the documented "only plain strings are ever
  retained" guarantee is actually asserted rather than assumed. The measured
  end-to-end cost of that choice is +0.4 ms on a pass that the rest of this
  change takes from ~5,772 ms to ~6.8 ms.
- `pattern_verdicts._emit_lost_invariants` shares one public-use index across
  its whole loop instead of rebuilding it per OPAQUE_POINTER-tagged type. That
  loop is the predicate's other production consumer, and `_public_pointer_only`
  is a one-shot entry point that rebuilds on every call.
- The normalisation cache serialises every read and write under one lock. The
  individual `OrderedDict` operations are atomic under the GIL but the
  *sequence* is not: a hit that looks up a key and then marks it
  most-recently-used can have that key evicted by another thread in between,
  and `move_to_end` then raises `KeyError`. That race did not exist under
  `functools.lru_cache`; it was introduced by taking the cache into Python to
  make its contents inspectable.
