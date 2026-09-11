### Fixed

- **`check-target`'s assurance-overlay `compile:` merge no longer
  mis-orders a `compile.defines`/`compile.options` conflict that spans
  different fields with no `compile.std` involved.** When the checkout
  document set `compile.defines` and the sources-root document expressed
  the same macro via `compile.options` (or vice versa), the overlay's
  merged document rendered the sources-root token AFTER the checkout
  token, letting sources-root silently win the compiler's last-flag-wins
  rule — the reverse of the documented checkout-wins precedence. The
  previous fix (`cli_options.merge_compile_std_fields`) only widened its
  trigger to "a `compile.std` value co-occurs with `defines`/`options`",
  which still missed this `defines`-vs-`options` combination since neither
  document set `std`. The joint fold now triggers whenever BOTH documents
  contribute anything among `compile.std`/`defines`/`options`, regardless
  of which of the three fields each side uses, matching
  `merge_compile_config`'s real two-stage precedence in every field
  combination.
