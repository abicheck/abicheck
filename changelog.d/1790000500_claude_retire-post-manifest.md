### Removed

- **`compare --post-manifest` is gone** (one-comparison-product Phase 9d).
  A POST Python export manifest is a project property: set
  `contract: {overlays: {post_manifest: PATH}}` in `.abicheck.yml` (a
  relative path resolves against the project root). The flag exits `64`
  (`No such option`). With it, `--contract` is `compare`'s one contract
  mechanism.
