### Changed

- **The `--stat` JSON, root-cause JSON and Markdown source modules take their policy facts from `report/`** — `reporter.py` and `reporter_markdown.py` now resolve the effective kind sets, severity gate, run summary, release recommendation and `--require-complete-analysis` assurance block through `abicheck/report/policy_facts.py` instead of calling the policy and semver owners directly. Output is byte-identical; the policy reach of these modules is now recorded by the architecture ratchet, which shrinks as the Markdown renderers stop deciding.
