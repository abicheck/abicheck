### Changed

- **The six model types that dominate an L2 snapshot no longer carry a
  per-instance `__dict__`.** `Function`, `Param`, `Variable`, `RecordType`,
  `EnumType` and `Fact` are now `@dataclass(slots=True)`.

  Measured, not assumed. A real castxml header dump of a compiled C++
  fixture decomposed by object *identity* (each object charged once, so a
  shared object is not counted per reference) showed the snapshot cost
  ~92 objects and ~9 KB per function, stable across a 4x change in fixture
  size. `functions` was 54% of the whole, and `dict` was 72% of that — not
  strings, not ABI payload. Per instance: a `Function` is 48 bytes of object
  and 1,584 bytes of `__dict__`, so 97% of its cost was the hash table, for a
  dataclass whose 48 fields are fixed at class-definition time.

  | type | fields | before | after |
  |---|---|---|---|
  | `Function` | 48 | 1,632 B | 416 B |
  | `Param` | 10 | 344 B | 112 B |
  | `Fact` | 4 | 232 B | 64 B |

  End to end on the same real dump, per function so fixture counts cancel:
  **8,990 -> 5,972 bytes (-34%)** and **139,310 -> 107,991 objects (-22%)**,
  with the `functions` field alone down ~54% and `dict` gone from its
  dominant types entirely. A release fan-out holds two of these per member,
  concurrently, which is why a per-instance saving shows up as a bundle-peak
  saving.

  `AbiSnapshot` is deliberately **not** slotted: it is one instance per
  snapshot, so there is nothing to save, and real code depends on its
  `__dict__` — `compare/surface_reconcile.py` and `compare/template_surface.py`
  stash per-comparison memo caches there, and `report/build.py` walks it with
  `vars()`. The leaf types carry none of that, which is what makes them
  slottable and it not.

  Four preconditions were checked rather than assumed: none of the six has a
  `cached_property` (that decorator needs a `__dict__`, and is confined to the
  `*_facts` metadata classes, which are one-per-snapshot and untouched), none
  is subclassed anywhere, each `__post_init__` assigns only declared fields
  (so the legacy/`Fact` bridging still works), and no caller assigns an
  undeclared attribute to one. The interaction worth naming: the `--depth`
  projection relies on `copy.copy` carrying a whole instance without re-running
  `__init__`, and its 43 ownership tests still pass — `copy` goes through the
  slots reducer instead of `__dict__`, with the same result.
