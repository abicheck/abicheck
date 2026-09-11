### Fixed

- **`check-target`'s assurance-overlay generation no longer double-folds a
  sources-root `compile:` block for a stored-baseline `compare`.** When
  `analysis-assurance-complete: true` synthesized a `--config` overlay for a
  `mode: compare` run against a stored baseline, the "Generate
  assurance-overlay config" step promoted a `--sources` tree's own
  `compile:` block into that overlay as a per-field merge onto the checkout
  document's `compile:` — believing it needed to reproduce the real
  pipeline's own checkout-then-sources two-stage merge itself. It didn't
  need to: `frontends/cli/commands/compare.py`'s `_embed_inline_source_side`
  already performs that second merge stage unconditionally, regardless of
  whether `--config` is explicit, so the overlay's own promotion folded the
  same sources-root document's `compile:` block in a second, redundant time.
  A repeat-sensitive flag (`compile.options: [-include, ...]`) could
  therefore be applied twice in the final compiler invocation purely because
  the acceptance gate was enabled. The overlay generator (and
  `action/run.sh`'s equivalent compile-context overlay) now leaves
  `compile:` untouched at the checkout-only value for this shape, letting
  the CLI's own single, unconditional fold run alone — matching this
  repository's documented per-shape block-ownership rules.
