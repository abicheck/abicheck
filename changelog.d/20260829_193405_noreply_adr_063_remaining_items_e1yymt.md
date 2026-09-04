### Fixed

- **A top-level by-value cv-qualifier on a function template's return
  type no longer collapses onto the same `EntityId`.** Unlike an
  ordinary function, where that qualifier is dropped and two such
  declarations are a redefinition error, a function template's return
  type keeps it as a genuine, standard-permitted overload discriminator
  (`template<class T> T f(T);` and `template<class T> const T f(T);`
  are two real, coexisting templates). The unmangled-template identity
  fallback's return-type canonicalization now preserves that qualifier
  instead of dropping it the way an ordinary parameter's does.
