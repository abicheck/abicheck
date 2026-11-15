### Fixed

- `default_template_arg_changed` no longer explains instantiation churn as a
  changed default without evidence of one. A removed instantiation is paired
  only with a newly added one (never a sibling that already existed, such as
  `ktwice<int>` beside a dropped `ktwice<double>`), and a single-argument
  template no longer qualifies, since a renamed tag type (`kfam<ktag_a>` ->
  `kfam<ktag_b>`) reads the same. A real single-parameter default change is
  reported from header evidence as `template_param_default_changed`.
