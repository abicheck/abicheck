### Removed

- **Agent Skills portfolio reset to one internal candidate.** ADR-058's
  2026-08-20 amendment removed `native-api-evolution`,
  `native-consumer-compatibility`, and `native-release-compatibility` from
  the published Agent Skills surface (`skills-src/`, `.agents/skills/`,
  `.claude/skills/`, `.gemini/skills/`) — none had measured evidence of
  improving agent behavior over a well-documented CLI, and publishing three
  more unvalidated skills alongside the one flagship was scaling packaging
  ahead of validated product value. The sole surviving skill,
  `native-binary-compatibility-review`, is renamed `review-native-library-change`
  and marked an internal candidate, not yet for external publication. The
  three removed skills' source remains recoverable from git history.
