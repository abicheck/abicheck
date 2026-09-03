### Fixed

- **A nested template-template parameter's own non-type parameter no
  longer changes identity when only an ENCLOSING parameter is renamed.**
  `template<class T, template<T> class TT> void f();` is valid C++ — a
  nested, unnamed non-type parameter inside `TT` can legally reference
  the enclosing `T` — but the clang header backend's recursive
  parameter-kind walk started each nested `TemplateTemplateParmDecl`'s
  own substitution scope empty, so the nested parameter's dependent type
  spelling (`T`/`U`) was never canonicalized against the enclosing
  parameter's rename, fingerprinting the identical declaration as two
  different overloads. The recursive descent is now seeded with every
  enclosing parameter name already visible.
