### Performance

- **Header/source contract evidence is judged only for reported findings** —
  the experimental-namespace, mandatory-template-parameter and
  unspecified-return detectors no longer ask the ADR-084 evidence question of
  every indexed declaration; they ask it once per reported subject. The
  class-template specialization rule reads each removed symbol's mangled name
  once instead of twice. Findings and their reachability tags are unchanged.
