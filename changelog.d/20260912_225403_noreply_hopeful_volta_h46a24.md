### Fixed

- `RenderOptions.show_recommendation` now defaults to `True`, matching
  `render_output`'s own default. The two disagreed, so building a
  `ReportEnvelope` directly and projecting it silently dropped the Release
  Recommendation section that every consumer going through `render_output`
  gets.
