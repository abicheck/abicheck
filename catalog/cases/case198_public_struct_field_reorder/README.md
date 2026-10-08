# Case 198: Public Struct Field Reorder

**Category:** Breaking | **Verdict:** ❌ BREAKING

## Verdict and consumer impact

`Record`'s first two fields swap places. Nothing is added, nothing is
removed, and `sizeof(Record)` is unchanged — so a size-only check reports a
clean release. Every consumer already compiled against v1 still loads `id`
from offset 0, which under v2 holds `flags`. The result is a silent misread
(and a silent miswrite, for a consumer that fills the struct itself), with
no crash and no link error to notice it by.

This is the **positive control** for the field-reorder mechanism. Its
negative control is
[`case120_internal_struct_reordered_scoped`](../case120_internal_struct_reordered_scoped/README.md),
where the identical reorder is applied to a type that is *not* on the public
surface and must therefore stay `NO_CHANGE` under public-header scoping (the default).
The pair proves two things about these two fixtures: the public reorder is
flagged, and the internal one is not.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `int id; int flags; long timestamp;` | `int flags; int id; long timestamp;` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

type_field_offset_changed: Field offset changed: Record::id (0 → 32 bits)
type_field_offset_changed: Field offset changed: Record::flags (32 → 0 bits)
```

## Minimum evidence

`min_evidence: L1` — the fact abicheck needs is each member's byte offset,
which a `-g` build records in DWARF (`DW_AT_data_member_location`). The
exported symbol table alone (L0) carries no record layout at all, so an
L0-only comparison sees an unchanged export set and reports nothing.

## Why abicheck catches it

`diff_types.py` matches the two `Record` declarations by name and compares
each field's recorded offset rather than only the record's total size, so a
size-preserving permutation is still a difference. `type_field_offset_changed`
is `BREAKING` by default in the change registry because an offset is exactly
what a compiled consumer hard-codes.

## Runtime failure demonstration

**Severity: silent data corruption — no crash, no link error.**

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -g app.c -L. -lv1 -Wl,-rpath,'$ORIGIN' -o app
./app            # consumer sees id = 42, library sees id = 42     exit 0
gcc -shared -fPIC -g v2.c -o libv1.so   # swap in v2, no recompile
./app            # consumer sees id = 0  (it read `flags`)         exit 1
```

The witness asserts the contract (`id` reads back as `42` through the
consumer's own layout and through the library) and exits 1 on a misread; it
used to print `MISREAD` and still exit 0.

**Source side (`api_break: false`).** Consumers that access fields by name
recompile cleanly against v2, which is why the case records no API break.
Consumers that use *positional* aggregate initialisation
(`Record r = {42, 0, 0};`) also recompile without a diagnostic but now put
`42` into `flags` — a silent source-level behavior change worth reviewing for
such code; it is not demonstrated by this fixture.

## Safe redesign

Append new fields at the end of the record and never permute the existing
ones; if the layout genuinely has to change, make the type opaque behind
accessor functions (see
[`case80_pimpl_shared_to_unique`](../case80_pimpl_shared_to_unique/README.md))
or bump the SONAME. Reordering is safe only for a type that never crosses the
public boundary — which is precisely what case120 demonstrates.

## Cross-tool comparison

`abidiff` reports the same offset changes from DWARF; ABICC reports them from
its own header dump. The distinguishing question is not whether a tool sees
the reorder but whether it can tell a *public* reorder from an *internal* one
— the case120/case198 pair is what pins abicheck's answer to both.
