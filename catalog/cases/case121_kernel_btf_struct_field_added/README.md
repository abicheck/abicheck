# Case 121: Kernel BTF Struct Field Growth

**Category:** Kernel BTF / Type Layout | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

Linux kernels embed type layout in **BTF** (BPF Type Format) — the `.BTF`
section of `vmlinux`, produced by `pahole -J`. Out-of-tree kernel modules
and eBPF/CO-RE programs are compiled against one kernel's view of a struct;
if a later kernel grows that struct (adds a field, changing `sizeof`), code
built against the old layout reads/writes at the wrong offsets. Here the
kernel struct `task_state` goes from **2 fields → 3 fields**, so
`sizeof(task_state)` changes 8 → 12 bytes. An out-of-tree module recompiled
against the new kernel headers is fine; one still loaded from the old
build (or a non-CO-RE eBPF program with the old offsets baked in) reads
past the layout it expects or writes into memory the new kernel now uses
for the third field — the classic "module vs. `vmlinux` BTF" ABI break.

## Old/new diff

| | `v1.btf` | `v2.btf` |
|---|---|---|
| `task_state` fields | `f0`, `f1` (8 bytes) | `f0`, `f1`, `f2` (12 bytes) |

`v1.btf`/`v2.btf` are committed, hand-assembled BTF blobs (the same
on-disk format `pahole -J` / `bpftool btf dump` emit) — regenerate them
with `python gen_btf.py`. There's no `v1.c`/`v2.c` or `app.c` here: this
case models kernel type-layout drift, not a compilable userspace library.

## abicheck command

```bash
abicheck compare v1.btf v2.btf
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- struct_size_changed: Struct size changed: task_state (8 → 12 bytes)
  > sizeof(T) changed in debug info; confirms layout break visible at
    binary level.
```

(The real run also reports LOW confidence and a long list of coverage
gaps — expected for a bare type-metadata blob with no ELF/symbol table
attached; the layout detector that flags `struct_size_changed` doesn't
need any of that.)

## Minimum evidence

`min_evidence: L1` — BTF carries the same kind of struct-layout facts as
DWARF (total size, per-member offsets), so no source headers are needed to
detect the size change; the committed blob's type records alone are the
floor.

## Why abicheck catches it

`parse_btf_from_bytes` parses the BTF type section into the same
type-metadata model DWARF parsing produces, then hands it to
`AbiSnapshot.dwarf`; `compare()` runs the identical struct-layout
detectors against it that it would against a DWARF-derived snapshot —
`task_state`'s recorded byte size (`DW_AT_byte_size`-equivalent) differs
between `v1.btf` and `v2.btf`, so `struct_size_changed` fires the same way
it would for a compiled `.so`. BTF-vs-DWARF is an input-format difference,
not a different detection path.

## Runtime failure demonstration

There's no `app.c` here — the consumer isn't a userspace process linking a
`.so`, it's kernel code. The real failure mode: an out-of-tree module (or
a non-CO-RE eBPF program that reads `task_state` fields by hard-coded
offset) is built against a kernel where `task_state` is 8 bytes. Loaded
into — or, for eBPF, verified against — a kernel where `task_state` has
grown to 12 bytes, any access to a field at or past the old struct's end
reads/writes memory the new kernel layout no longer means what the module
thinks it means: adjacent kernel state gets corrupted or misread, with no
"undefined symbol"-style load-time error, because kernel struct layout
compatibility isn't checked by the loader — it's checked (for CO-RE eBPF)
by field-relocation logic reading BTF at load time, or not checked at all
for a plain out-of-tree module.

## Safe redesign

Never grow a struct the kernel exposes as stable UAPI/kABI without a
version-gated accessor or explicit opt-in (the same discipline as case07's
opaque-pointer guidance, applied to kernel structs). For eBPF specifically,
write CO-RE programs (`BPF_CORE_READ`, `bpf_core_field_exists`) instead of
hard-coded offsets — CO-RE relocations resolve each field's real offset
against the *running* kernel's BTF at load time, so a struct grown between
kernel versions doesn't silently misalign a program compiled against an
older layout.

**Real-world example:** this is exactly the problem BPF CO-RE (Compile
Once – Run Everywhere) was built to solve — pre-CO-RE eBPF programs baked
in fixed struct offsets from the build kernel's headers and broke across
kernel struct layout changes; CO-RE's BTF-based relocations are the fix.

## Cross-tool comparison

`abidiff`/`abi-compliance-checker` compare ELF+DWARF (or CTF) shared
objects — neither has a documented workflow for diffing a bare,
compiler-independent BTF blob the way `parse_btf_from_bytes` does here.
Reproducing this comparison with those tools would mean first embedding
each BTF blob into a real `vmlinux`/module build and extracting debug
info from that, not a direct `v1.btf`/`v2.btf` diff, so no equivalent
invocation is given here.
