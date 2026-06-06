# case121 — Kernel BTF struct grows a field (out-of-tree module break)

**Verdict:** `BREAKING` · **Kind:** `struct_size_changed` · **Platform:** Linux (kernel BTF)

## What this case encodes

Linux kernels embed type layout in **BTF** (BPF Type Format) — the `.BTF`
section of `vmlinux`, produced by `pahole -J`. Out-of-tree kernel modules and
eBPF/CO-RE programs are compiled against one kernel's view of a struct; if a
later kernel **grows that struct** (adds a field, changing `sizeof`), a module
built against the old layout reads/writes at the wrong offsets — the classic
"module vs `vmlinux` BTF" ABI break.

Here the kernel struct `task_state` goes from **2 fields → 3 fields**:

| | `v1.btf` | `v2.btf` |
|---|---|---|
| `task_state` fields | `f0`, `f1` (8 bytes) | `f0`, `f1`, `f2` (12 bytes) |

`sizeof(task_state)` changes 8 → 12, so abicheck reports `struct_size_changed`
(BREAKING) — exactly the layout detectors it uses for DWARF, because BTF is
converted to the same type-metadata model.

## Files

- `v1.btf`, `v2.btf` — committed minimal BTF blobs (the on-disk format
  `pahole -J` / `bpftool btf dump` emit).
- `gen_btf.py` — regenerates the blobs (`python gen_btf.py`).

## Reproduce

```bash
# From committed fixtures (no kernel toolchain needed):
abicheck compare \
    examples/case121_kernel_btf_struct_field_added/v1.btf \
    examples/case121_kernel_btf_struct_field_added/v2.btf

# From a real kernel:
pahole -J vmlinux                       # embed .BTF
bpftool btf dump file vmlinux format raw
```

A real kernel would carry the BTF inside `vmlinux`'s `.BTF` ELF section;
`abicheck` extracts and diffs it the same way (`--btf` / autodetection). See
[Kernel & eBPF (BTF)](../../docs/user-guide/kernel-btf.md).
