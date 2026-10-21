### Changed

- **Report renderers' policy imports are pinned by a ratchet test** — `report/render_markdown_document.py` and `report/render_markdown_alternate.py` still reach `policy` and recompute the disposition audit as a fallback for direct callers. A new test records exactly that set, so any new renderer-side decision fails CI and the allowlist can only shrink. Rendered output and every exit code are unchanged.
- **Cross-format parity test covers document headline facts** — the verdict, exit code, detected/reclassified counts and the rule that moved a finding are asserted to agree across JSON, Markdown and HTML from the same `ReportDocument`.
