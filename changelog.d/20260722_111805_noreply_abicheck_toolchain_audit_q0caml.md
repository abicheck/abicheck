### Fixed

- **The C++20 dialect detector no longer misdetects a statement-level call
  to a pre-C++20 function named "requires".** A call like `requires(1);`
  inside a function body was still matched as a requires-expression — the
  earlier fix only excluded `requires(` preceded by a bare identifier (the
  declaration case), not preceded by nothing but a statement boundary
  (`{`/`}`/`;`, or the start of the scanned text), which is what a bare
  call-as-statement looks like.
