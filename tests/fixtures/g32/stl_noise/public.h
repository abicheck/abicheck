// G32 Phase 0 "external STL noise" fixture (see ../README.md, Fixture 3).
// A public function taking a standard-library container by value, so the
// merge layer has a supporting (non-reportable) STL instantiation alongside
// a genuinely public declaration to distinguish (ADR-024's
// supporting-vs-reportable boundary, exercised here rather than redefined).
#include <vector>

int sum_all(std::vector<int> values);
