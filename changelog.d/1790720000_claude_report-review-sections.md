### Added

- **Versioning policy verdict in every compare report** — when the policy file states a `versioning:` block, `release_recommendation.policy_acceptance` in the JSON report is now filled in, and the Markdown report, the review digest and the HTML report state whether the release is accepted under the declared promise and enforcement. The verdict, findings and exit code are unchanged by it.
- **Consumer impact in HTML reports** — `compare --use-cases` now works with `-o html=...`, adding a "Consumer impact" section that lists, per declared use case, the findings attributed to it and how many findings no declared entrypoint reaches.
- **`project history -o html`** — renders the history as a page with a per-release verdict table, a lifecycle timeline (added, deprecated and removed entities, with shaded intervals for possibly missing releases) and the complete event list as a table.
