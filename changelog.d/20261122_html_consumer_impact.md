### Added

- **HTML reports state "N of M consumers affected".** A `compare --used-by`
  HTML report's consumer-scoped box now shows `consumer_impact_summary`
  (affected / evaluated / supplied consumers, and any unreadable consumer
  paths), matching JSON, SARIF and JUnit.
