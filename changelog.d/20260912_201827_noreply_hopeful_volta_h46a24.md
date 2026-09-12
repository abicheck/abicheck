### Removed

- **`abicheck.cli`'s lazy compatibility-alias surface** — the root CLI module
  carried a name-to-owner table (`frontends/cli/moved.py`) of 82 private
  helpers that had physically moved to sibling modules, resolved at attribute
  access through a module-level `__getattr__`, plus a custom module class that
  raised on assignment to any of those names. That guard existed because a
  `monkeypatch.setattr` against a lazily-resolved alias froze it and silently
  defeated every later patch of the real owner — a real, order-dependent CI
  failure. All 109 call sites across 9 modules and 25 test files now import
  from the owning module, so the table, the resolver and the guard are deleted
  together. **Import from the owner, and patch the owner**: `abicheck.cli`
  re-exports nothing and `getattr` on a retired name raises the ordinary
  `AttributeError`. Two modules were reaching *their own* functions through
  the facade purely to be patchable there; they now call them directly.
