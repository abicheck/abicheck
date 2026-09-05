// case109 v2 — policy tags renamed to oneTBB-2021-style names.
//
// `queueing`  → `buffering_policy`
// `rejecting` → `backpressure_policy`
//
// The instantiation anchor `queue_node` is also gone — replaced by a new
// alias with the new policy name. Consumer source that referenced the old
// names fails to compile.
#pragma once

namespace mylib { namespace flow {

struct buffering_policy   {};
struct backpressure_policy{};

template <class Policy>
class function_node {
public:
    function_node() = default;
    int run(int x) { return x + 1; }
};

// New name — old `queue_node` is removed.
typedef function_node<buffering_policy> buffer_node;

}} // namespace mylib::flow
