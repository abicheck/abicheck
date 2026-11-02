### Fixed

- **A contract type graph whose names hold a lone surrogate no longer fails
  to build** — a declaration or type name decoded from a JSON `\ud800`
  escape made the packed `TypeGraphSnapshot` (ADR-062 A2.5) raise
  `UnicodeEncodeError`. Every code point now round-trips exactly, and two
  separate surrogate code points stay distinct from the one character they
  would spell as an escape pair.
