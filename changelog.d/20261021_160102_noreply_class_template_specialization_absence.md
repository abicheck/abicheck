### Fixed

- **A class-template specialization the new header stops instantiating is no
  longer reported as removed** — while its primary template is still declared,
  `Box<double>` no longer reports `type_removed`, and its implicit
  constructors, destructor and `operator=`, inline methods and static data
  members no longer report `inline_function_removed`/`func_removed`/
  `var_removed`. Exported members are judged by the new export table, as for
  function templates: an export the new binary dropped is
  `func_visibility_changed` or `var_visibility_changed` (BREAKING). A deleted
  explicit specialization, a removed template and a removed non-template class
  still report removals. The castxml dump now records whether a specialization
  was instantiated from its primary template
  (`RecordType.specialization_declared_fact`, snapshot schema v61); an old
  snapshot without it keeps the previous findings.
