---
doc_type: how-to
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - target-ownership
depends_on:
  - abicheck/extract/ownership.py
  - abicheck/extract/ownership_stamp.py
  - abicheck/model/extraction_scope.py
  - abicheck/buildsource/build_config_scope.py
lifecycle: active
---

# Target Ownership: "My headers pull in other people's declarations"

A header parse sees everything your public headers `#include`: your own
API, a vendored `fmt`, a sibling component's headers, the standard
library. abicheck records **whose** each declaration is, and what your
library **promises** about it, so a dependency's declaration is not judged
as your export obligation. This page walks the setup end to end.

## 1. Declare the roots

Ownership comes from **files**, never from names. In `.abicheck.yml`:

```yaml
scope:
  public_header_dirs: [include/svs/]          # your library's headers
  dependencies:                                # headers that are not yours
    - name: fmt
      header_roots: [include/svs/third-party/fmt/include/]
  private_headers: [include/svs/*/detail/**]   # yours, but not promised
  private_namespaces: [svs::detail]
```

- A `-H` **directory** also counts as one of your roots. A `-H` *file* does
  not, and a `-I` directory never does: include paths are compile context.
- The most specific root wins, so a dependency vendored inside your tree
  (the `fmt` above) belongs to the dependency.
- An explicit root beats the "system header" heuristic: a library installed
  under `/usr/include/svs/` is still yours.
- A file no root claims is recorded as `unresolved`. It is kept and
  reported, never silently treated as private.

The full precedence list is in the
[`scope:` reference](../reference/config-file.md#scope).

## 2. Preview before you trust it

```bash
abicheck dump libsvs.so -H include/svs/ --dry-run
```

With an ownership key set, the dry run prints the rules and the owner and
contract of every `-H` header, warning about any `-H` header that is not
public target API (for example, one that falls under a dependency root).

## 3. Dump and read what was recorded

A real dump classifies **every** declaration once and stores the answer in
the snapshot's `extraction_scope` block (schema v52): the rules, their
fingerprint, and one owner/contract/rule per declaration. See
[the snapshot format](../reference/snapshot-format.md#extraction-scope-and-ownership-schema-v52).

| Owner | Contract | Meaning |
|---|---|---|
| `target` | `public` | Your API: judged as your contract. |
| `target` | `private` | Yours, under `private_headers`/`private_namespaces`: kept, not promised. |
| `dependency:<name>` | `external` | A declared dependency's: you promise nothing about it. |
| `toolchain` | `external` | Standard library, system headers, compiler builtins. |
| `unresolved` | `unresolved` | No root claims the file. Declare one. |

## 4. What changes in the report

- A declaration whose contract is `private` or `external` **owes no
  export**, so it produces no `public_not_exported` finding. A component's
  headers that ride along in one shared header tree (the Intel MKL / oneDAL
  shape) stop being demanded from a binary that was never meant to export
  them. The same rule applies to a single library, a one-member package and
  a multi-library release, where the release-level check reconciles the
  contract against the union of every member's exports.
- The resolved configuration records where each ownership input came from
  (`-H`, `.abicheck.yml`, or a typed `InputSpec.ownership`), in the
  contract-context receipt's `surface.ownership`.
- Nothing is dropped. The dependency's declarations are still parsed and
  still compared. Only their classification is recorded. Keeping fewer of
  them (`dependency_evidence: referenced`) is a later phase and is rejected
  today.
- The rules' fingerprint is part of the configuration digest
  (`surface.ownership`), so two runs classified differently do not share a
  digest.

## 5. Comparing against an older baseline

Both sides of a `compare` are classified under the same project config. A
stored baseline keeps the rules it was dumped under:

- **Same rules:** compared as usual.
- **Different rules:** still compared, because nothing was kept or dropped
  differently. The report lists the declarations whose owner or contract
  moved, so a finding that changed classification is explained.
- **Baseline older than schema v52:** compared, with a note that its
  ownership was not recorded. Re-dump it to remove the note.

A pair whose sides kept dependency declarations differently is refused.
See [Troubleshooting](troubleshooting.md).

## Why not a namespace filter?

Filtering by namespace looks like the obvious lever, and it loses real API.
Measured on real targets
([plan M2](../contribute/plans/target-ownership-and-extraction-scope.md#m2-what-each-narrowing-loses)):
SVS declares `fmt::formatter<svs::…>` specialisations inside `fmt`, and
oneDAL's `extern "C"` functions live at global scope. Neither has a name
under the target's namespace, and a file-based rule keeps both.
`private_namespaces` narrows the contract of declarations you already own.
It never grants ownership.
