# Semantic-facts AST capture

`ast_dump.json` is clang 18.1.3's raw `-ast-dump=json` of `header.h`
(compacted, sticky locations as clang writes them), regenerated with:

```sh
clang++ -x c++ -std=c++17 -fsyntax-only -Xclang -ast-dump=json header.h \
  | python -c "import json,sys;json.dump(json.load(sys.stdin),sys.stdout,separators=(',',':'))" > ast_dump.json
```

`header.h` holds one of each fact family the header pass and the L5 pass share
through `abicheck/buildsource/semantic_fact_folds.py`: a class template with a
partial specialization, a function template with an explicit specialization,
virtual overrides, and function-pointer callback registrations. It lets
`tests/test_semantic_fact_folds.py` run without a compiler.
