### Performance

- **Suppression evaluates each rule's selectors once per finding.**
  `SuppressionList.evaluate` called `matches`, `would_withhold` and
  `would_withhold_unknown_reachability` per rule, and each re-ran the full
  selector match (and, for a `finding_id` rule, the canonical finding id) --
  up to three times per rule per finding. It now tests the selectors once and
  applies the gates to that answer. Namespace selectors also cache a name's
  demangled, template-stripped forms instead of re-deriving them per rule.
  With 40 non-matching rules on the synthetic sweep (n=8000) suppression went
  27.1 s -> 8.9 s thread CPU; outcomes, rule provenance and diagnostics are
  unchanged.
