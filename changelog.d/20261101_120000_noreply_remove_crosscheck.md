### Removed

- **The `--crosscheck` option is gone entirely, with no deprecation period.**
  The Action's `crosscheck` input (previously a tombstone that failed the run)
  is no longer declared; the reserved `.abicheck.yml` `crosschecks:` key is now
  an unknown-key error; and the compare report's `exit` block drops the
  always-`0` `crosscheck_promotion_contribution` key and the
  `promoted_crosscheck` reason (report schema **7.0**, a major bump). Every
  cross-source check already reaches `compare` as an ordinary finding:
  control one check's severity with `.abicheck.yml`'s
  `policy.overrides.<CHANGE_KIND>: error`.
