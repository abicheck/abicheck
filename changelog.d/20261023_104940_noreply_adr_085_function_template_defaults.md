<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Function-template default arguments are recorded on clang** — `template<class T = int> void f();` now records each template parameter's spelled default on the function pattern (snapshot schema v64, ADR-085 D1). A header-only change to a function template's default reads `API_BREAK` (`template_param_default_changed`), the same contract as a class template, and an overloaded template is paired by its identity so one overload's change never reports another. castxml keeps its `NO_CHANGE` verdict and reports the gap through its existing header scan. Pre-v64 snapshots load the defaults as unrecorded, so re-dump a baseline to compare them.
