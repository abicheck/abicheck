<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`docs/integration/scenarios/single-build-audit.md` pointed a reader at
  the unsafe CLI spelling in its own "when to move past this scenario"
  summary** (Codex review, round 12). The page's own earlier section
  establishes that `compare --no-baseline` crashes (an unhandled
  `AssertionError`) when the candidate has a real hygiene finding, and that
  the safe CLI path is `scan CANDIDATE` with no `--against` — but a later
  bullet named `--no-baseline` (CLI) as the counterpart of
  `baseline-channel: none` (Action), so a reader relying on that summary
  alone could select exactly the broken command. Reworded to name `scan`
  (no `--against`) and link back to the page's own explanation instead of
  spelling the unsafe flag.
