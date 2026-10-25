### Fixed

- **C++20-constrained template overloads no longer share one entity identity,
  and an out-of-line member definition no longer splits from its in-class
  declaration** (clang header backend). Two function templates that differ
  only in a `requires`-clause or a constrained template parameter
  (`template<C1 T>` vs. `template<C2 T>`, `C1<T> && C2<T>` vs.
  `C1<T> || C2<T>`, a trailing `requires` on a class-template member) now get
  distinct `EntityId`s. The concept is resolved from the location clang gives
  its `ImplicitConceptSpecializationDecl`, which is the `ConceptDecl`'s own,
  so no header text is read. An out-of-line member template or static data
  member definition (`template<class T> void A::f(T) {}`,
  `template<class U> int C<U>::s = 0;`) now carries the `EntityId` of the
  in-class declaration it redeclares, following clang's `previousDecl` link.
  Unconstrained declarations keep their identities. `EXTRACTOR_GENERATION`
  is raised (24); no stored spelling changes.
