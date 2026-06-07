# G6 — Kernel BTF & accelerator (SYCL) workflows

**Registry:** `UC-ARCH-kernel-btf` (`complete`), `UC-ARCH-sycl` (`complete`)
**Effort:** M · **Risk:** medium (fixture generation needs kernel/SYCL toolchains)

## Problem

- **Kernel / eBPF:** `abicheck/btf_metadata.py` and `ctf_metadata.py` parse the
  formats and are unit-tested; this plan closed the workflow coverage for the
  canonical use case — *"does this kernel module's view of a struct still match
  `vmlinux` BTF?"* (the out-of-tree-module ABI break). ADR-007 is "Proposed".
- **SYCL:** plugin-interface detection exists (`sycl_metadata.py`,
  `diff_sycl.py`, cases 82/83) but is not exercised at the workflow level; CUDA
  remains deferred by design.

## Goal & acceptance criteria

- [x] A BTF `compare` scenario: two BTF blobs where a kernel struct gains/loses a
      field (or a field type changes) produces the existing layout ChangeKinds
      (`struct_size_changed`, `struct_field_offset_changed`, …) through `compare`,
      with an asserted verdict in `ground_truth.json`.
- [x] A documented "module vs `vmlinux` BTF" workflow in the user guide
      (how to extract BTF with `--btf` and compare).
- [x] A SYCL workflow-level scenario: a plugin-interface change (mirroring
      case82/83) driven through the standard report path with an asserted
      verdict, not just the detector unit test.

## Design

1. **BTF fixtures:** generate two small BTF blobs (via `pahole -J` / `bpftool
   btf dump` on a tiny module, or hand-assembled) and feed them through the
   existing `--btf` dump path (`dumper.py` already routes BTF). The diff stage is
   format-agnostic, so layout detectors fire without new code.
2. **Workflow glue:** confirm `compare` accepts BTF-only snapshots on both sides
   (no headers); add a `--btf` example to `examples/` with a `gen_btf.sh`.
3. **Docs:** new `docs/user-guide/` section (kernel/eBPF) + link from
   `reference/platforms.md`. Re-evaluate ADR-007 status.
4. **SYCL:** add a scenario test that runs `diff_sycl` output through the
   reporter and asserts the grouped `sycl_overload_set_removed` /
   `cpu_dispatch_isa_dropped` findings end-to-end.

## Files & surfaces

- `examples/caseNN_kernel_btf_struct_change/` (BTF blobs + `gen_btf.sh` + README
  + ground truth).
- `abicheck/btf_metadata.py` / `dumper.py` (only if the BTF-only compare path
  needs hardening).
- `docs/user-guide/kernel-btf.md` (new), `mkdocs.yml` nav.
- `tests/test_btf_metadata.py` (extend to a compare-level assertion);
  SYCL scenario in `tests/test_diff_sycl.py`.

## Tests

- `@pytest.mark.integration` for BTF generation (needs `pahole`/`bpftool`); a
  pure-python path using committed BTF blobs as fixtures for the fast suite.

## Out of scope

CUDA device code (`.cubin`/PTX) — explicitly deferred. Full kernel-module symbol
namespace (`__ksymtab`) analysis beyond BTF type layout.
