# Scenario S24: Dependency, Container & Rootfs Checks

The question here isn't "did this library's ABI change" at all — it's "will
this binary actually **resolve** its dependencies" in a given rootfs,
container image, or sysroot. A perfectly ABI-compatible library still fails
this check if it (or something it depends on) simply isn't *present* where
the binary expects to find it. [ADR-047](../../development/adr/047-github-actions-integration-model.md)
§8's S24 is explicit that this is **not modeled as a library baseline scan**
— it's a separate command family, `deps tree`/`deps compare`, unchanged by
the rest of this integration model.

## `deps tree` — does this environment resolve?

```bash
abicheck deps tree ./app --sysroot /path/to/container/rootfs
```

Walks the binary's full transitive dependency closure and symbol bindings
against a given root — no baseline, no "old vs. new," just "does this
resolve here."

## `deps compare` — two environments, one binary

```bash
abicheck deps compare usr/bin/myapp --old-root /old-root --new-root /new-root
```

Diffs a binary's whole dependency stack — every library it transitively
pulls in — across two container images/sysroots/rootfs trees. This is the
per-library ABI diff extended across an entire dependency graph, for
"did upgrading the base image change what this binary actually links
against."

See [Companion Commands](../../user-guide/companion-commands.md) for the
full `deps tree`/`deps compare` reference (output formats, what each command
replaced from an earlier CLI generation).

## When to move past this scenario

- **You actually want an ABI/API comparison, not a resolution check** — any
  other scenario; `deps tree`/`deps compare` answer "does it link," not "is
  it compatible."

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [Companion Commands](../../user-guide/companion-commands.md) — the full `deps tree`/`deps compare` reference.
