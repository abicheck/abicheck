### Fixed

- A `var_added` for a const variable initialized in its declaration (a `static constexpr` member, implicitly inline since C++17, or a `static const` integral) that has no exported symbol now reads "header-initialized constant". It no longer says "not inline, so its definition is not shipped" (oneCCL `invalid_kvs_id`).
- The "N redundant change(s) hidden" note in the Markdown and HTML reports now gives each hidden change's real reason: folded into its symbol's existence finding, folded into a rename, or derived from root type changes, with per-reason counts when they differ. It used to give "derived from root type changes" for all of them.
- The review summary now links its retained non-gating count to the disposition audit's total: "(8 gating, 49 non-gating) + 10 non-gating held out of the findings". The two counts cover different populations and were shown with nothing connecting them.

### Documentation

- `ABICHECK_SIDE_PROCESSES=0` is documented consistently: both sides stay in one process, and the `compare` CLI then resolves them one after the other while the typed API uses two threads.
