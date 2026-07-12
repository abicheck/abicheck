// A classic project-prefixed include guard (does NOT derive from the
// filename) — exercises the structural include-guard fallback in both the
// plugin (isStructuralIncludeGuard) and the clang backend
// (clang.py::_is_include_guard) so a project-namespaced guard like this one
// is suppressed the same way a filename-derived guard is (case47 regression:
// previously only a name==<FILE>_H-shaped guard was recognized).
#ifndef DEMO_GUARDED_WIDGET_HPP
#define DEMO_GUARDED_WIDGET_HPP

namespace demo {
constexpr int kGuardedTag = 11;
}  // namespace demo

#endif
