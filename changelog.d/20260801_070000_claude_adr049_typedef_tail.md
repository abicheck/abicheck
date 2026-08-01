### Fixed

- **ADR-049 replay: a qualified typedef resolves only by its exact key.**
  `contract_evidence_collect` registered a typedef under its bare `::` tail
  as well, the way records and enums are registered — but the live
  public-surface walk follows a typedef with `snap.typedefs.get(name)`, a
  plain dict lookup with no tail fallback. A public signature spelling the
  bare `Alias` therefore reached a qualified `ns::Alias -> Secret` in the
  persisted graph, and a private `Secret` layout change the live evaluator
  recorded as `PROVEN_OUT_OF_CONTRACT` re-evaluated as `IN_CONTRACT` — a
  strengthened replay decision, which `compare_decisions()` treats as a
  defect. Affects only `compare --contract-evaluation`, which is advisory:
  no verdict, exit code, or finding set changes.

  Because the same snapshot now produces a different persisted type graph,
  `IDENTITY_ALGORITHM_VERSION` bumps to 2 so the two formats are
  distinguishable. Older (v1) contexts still load and replay; a v2 context
  is refused by a build that predates the fix, which is the fail-closed
  direction ADR-049 D6 specifies.
