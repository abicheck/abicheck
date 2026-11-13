### Fixed

- The conditional-field header scanner counted braces inside string and
  character literals, so a `"}"` in an initializer could close a record's
  scope early and misattribute its guarded fields.
- A stored release member whose assurance status is not a known value is now
  reported as `unknown`, with the original value in its notes.
- `BudgetedExecutor` rejects work submitted after `shutdown` even when it ran
  tasks inline; an unbalanced deadline registration-window exit now raises
  instead of silently losing a deferred SIGTERM.
