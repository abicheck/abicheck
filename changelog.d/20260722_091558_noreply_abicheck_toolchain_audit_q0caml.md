### Fixed

- **The C++20 dialect detector now ignores text inside C++11 raw string
  literals (`R"(...)"`/`R"tag(...)tag"`).** These were not recognized by the
  existing string-literal stripper (which only handles ordinary `"..."`
  literals), so their body was scanned as ordinary code — text merely
  resembling a requires-expression or concept declaration inside a raw
  string could force `-std=gnu++20` unnecessarily. This mattered more once
  the requires-expression scan started looking ahead across physical lines,
  since a multi-line raw string could span into that lookahead window.
