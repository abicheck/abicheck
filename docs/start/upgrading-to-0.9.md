---
doc_type: migration
audience:
  - library-maintainer
  - ci-owner
level: intermediate
lifecycle: migration
generated: false
---

# Upgrading from 0.8 to 0.9

Pin the scanner and the GitHub Action to the same release. The pip install
includes CastXML 0.8.0 on supported platforms. Teams upgrading from 0.7
should also apply the [0.8 CLI and configuration renames](upgrading-to-0.8.md).

## Replay a representative comparison

```bash
abicheck compare old/libfoo.so new/libfoo.so \
  --header old=old/include/foo.h --header new=new/include/foo.h \
  -o json=compare-0.9.json
```

Supply the public headers from each build. Header analysis now detects
withdrawn overloads and default arguments and distinguishes more changes to
by-value C++ arguments and returns. When a dependency enum changes, review
how its value reaches the public ABI before adjusting a gate. A DWARF record
with only a declaration can also qualify the reported assurance. See
[Understand your first report](first-report.md).

## Check automation that consumes reports

- For a removed ELF export, inspect the exact symbol variant, version and
  binding before attributing the loss to a shipped client. In a consumer
  manifest, set `artifact_role` to `released_client` for a shipped binary.
  A PIC relocatable object marked `control_object` can instead be linked
  against each provider when both library files and the linker are available.
- For two deployment directories, repeat `--used-by` to inspect more than one
  unchanged ELF client. The release report records the modeled bindings for
  each client. See [Check an old ELF client](../use/multi-binary.md#check-an-old-elf-client-in-both-release-directories).
- A library comparison can include `call_axis_shadow` for parameter or
  return changes. This experimental assessment describes the calling
  interface and has no independent effect on the exit code. Keep using
  the report's exit decision for CI.

The [CLI reference](../reference/cli-reference.md),
[Action inputs](../reference/github-action-inputs.md),
[configuration keys](../reference/config-keys-reference.md) and
[Python API reference](../reference/python-api-reference.md) document
the current interfaces.
