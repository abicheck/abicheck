### Fixed

- **`agent-evals/skills/skill-eval-pack.json`'s `skills` mapping regenerated
  correctly, restoring `check-abi-compatibility` as a published skill.** An
  earlier round's regeneration (triggered by a `skills-src/shared/
  compiler-and-build-profiles.md` content edit) landed with `"skills": {}`
  and empty `"affects"` lists on the `harness`/`trigger_corpus` shared
  hashes, instead of the correct `check-abi-compatibility` entries --
  `scripts/gen_skill_eval_pack.py --check` reproduces the diff directly
  (`skills.check-abi-compatibility: added`). This made every scenario- and
  shared-hash mapping in the pack fail `scripts/check_skill_eval_freshness.py`
  as "affects unpublished skill" / "affects no skill", since the published-
  skills set it validates against is read from this same (empty) `skills`
  key. Fixed by re-running `python scripts/gen_skill_eval_pack.py`, which
  correctly re-derives `skills.check-abi-compatibility` (with its own,
  content-accurate tree digest reflecting the prior round's genuine content
  change) while leaving every other already-correct hash/digest untouched.
