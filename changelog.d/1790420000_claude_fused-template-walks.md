### Performance

- The three clang template-parameter indexes (kinds, defaults, names) share
  one whole-AST traversal (`extract/headers/clang/template_decl_walk.py`) and
  replay their own registration over the collected `ClassTemplateDecl`s,
  instead of walking the document once each. Output is identical on real
  oneDAL ASTs; index construction takes 0.41-0.60 s instead of 0.86-2.43 s.
