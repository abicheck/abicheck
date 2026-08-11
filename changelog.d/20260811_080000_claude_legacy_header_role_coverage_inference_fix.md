<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A pre-role-stamping legacy snapshot lost trust for every established
  dependency role, not just genuinely new ones** (G29 Phase 5 item 5,
  Codex review): `_disagreeing_roles()` (the role-coverage version-skew
  guard) read a role as "not confirmed" whenever a side carried no exact
  role-coverage key for it — correct for a role that genuinely didn't
  exist yet in an older producer's own code, but wrong for a role that
  producer's walker always examined, just before the metadata mechanism
  recording it existed at all. Since a real producer's role-stamping is
  all-or-nothing (`_mark_role_coverage()` stamps its whole matrix in one
  unconditional pass — a producer can never confirm some role keys while
  genuinely lacking others), a side confirming a kind's family-level pass
  but carrying zero role keys for it can only mean one thing: a real,
  already-released snapshot from before role-key stamping existed at all.
  Comparing such a legacy snapshot against a modern one previously
  untrusted every role under the shared kind — including long-standing
  ones (`field`/`alias`/`var`/`return`/`param`/`base`/`ref`) that producer
  always examined — silently missing a genuine new dependency under any
  of them. Fixed by inferring coverage of those established,
  pre-role-stamping roles for a confirmed-but-zero-role-key side, while
  still treating a genuinely new role (`enum_underlying`/
  `template_param`/`default_template_arg`) strictly.
