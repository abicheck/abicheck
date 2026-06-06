# Kernel & eBPF (BTF) Workflows

The Linux kernel and out-of-tree modules describe their type layout with
**BTF** (BPF Type Format) rather than full DWARF. `abicheck` parses BTF from the
`.BTF` ELF section and feeds it through the **same layout detectors** as DWARF,
so struct/enum layout changes are detected format-agnostically.

The canonical use case is the **out-of-tree module ABI break**: *does this
module's view of a kernel struct still match the kernel it loads into?*

---

## Extracting & comparing BTF

`abicheck` reads BTF directly from any ELF that carries a `.BTF` section
(`vmlinux`, a `*.ko` module, or a BTF blob). Force the BTF debug format with
`--debug-format btf`:

```bash
# Two kernels / two module builds — compare their BTF type layout:
abicheck compare vmlinux-5.10 vmlinux-5.11 --debug-format btf
```

A kernel struct that **gains or loses a field**, or whose field **type/offset
changes**, surfaces as the usual layout findings (`struct_size_changed`,
`struct_field_offset_changed`, …) with a `BREAKING` verdict — exactly as a
DWARF struct change would.

### Module vs `vmlinux` BTF

To check an out-of-tree module against the kernel it targets, compare the
module's BTF against the target kernel's BTF for the structs the module touches:

```bash
# Old kernel the module was built against vs the new kernel it will load into:
abicheck compare vmlinux-built-against vmlinux-target --debug-format btf
```

If a struct the module embeds or passes by value changed layout between the two
kernels, the module's compiled assumptions are stale and the comparison reports
a binary break.

### Generating BTF

BTF is emitted by the kernel build (`CONFIG_DEBUG_INFO_BTF`) and can be produced
or extracted for individual objects with `pahole -J`, `gcc -gbtf`, or
`bpftool btf dump file <elf>`. Any ELF with a `.BTF` section works as a
`compare` input.

### Comparing raw BTF/CTF blobs directly

You don't need a full ELF: a **bare type-info blob** (a `.BTF` or CTF section
extracted with `bpftool btf dump file <elf> format raw` or
`objcopy -O binary --only-section=.BTF`) is accepted directly as a `compare`
input — `abicheck` recognizes the BTF (`0xEB9F`) / CTF (`0xCFF1`) magic:

```bash
abicheck compare task_state.v1.btf task_state.v2.btf
```

The committed example
[`examples/case121_kernel_btf_struct_field_added`](https://github.com/napetrov/abicheck/tree/main/examples/case121_kernel_btf_struct_field_added)
is exactly this: two BTF blobs where `task_state` grows from 2 to 3 fields,
reported as `struct_size_changed` / `BREAKING`.

---

## Accelerator stacks (SYCL / oneAPI)

`abicheck` understands the **SYCL plugin interface** (PI / UR): the set of
plugin entry points (`piPluginInit`, `urAdapterGet`, …), plugin libraries,
and backend/driver requirements that a runtime loads. A dropped or renamed
plugin entrypoint is a runtime-load break and is reported through the standard
`compare` path:

```bash
abicheck compare libsycl.so.7 libsycl.so.8
```

Removing a PI entrypoint (`sycl_pi_entrypoint_removed`) or a whole plugin
(`sycl_plugin_removed`) yields a `BREAKING` verdict that flows into the JSON,
SARIF, and Markdown reports like any other finding.

> **Scope:** CUDA device code (`.cubin`/PTX) is explicitly out of scope; see
> [Limitations](../concepts/limitations.md).
