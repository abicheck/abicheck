// case109 v1 — flow::graph node policy tags (oneTBB pre-2021 names).
//
// Mirrors oneTBB's historical `flow::graph` API, where node policies were
// distinct types living in the `flow::` namespace:
//
//   tbb::flow::queueing  // FIFO node policy
//   tbb::flow::rejecting // back-pressure node policy
//
// oneTBB 2021 renamed and relocated several of these tags. Source code
// that wrote `tbb::flow::queueing` no longer compiles, even though the
// underlying templates kept the same instantiated symbols.
#pragma once

namespace mylib { namespace flow {

// Policy tag types — selected as template parameters at consumer sites.
struct queueing  {};
struct rejecting {};

// A toy node template parameterized by policy.
template <class Policy>
class function_node {
public:
    function_node() = default;
    int run(int x) { return x + 1; }
};

// Public typedef used by tests / consumers as an instantiation anchor.
typedef function_node<queueing> queue_node;

}} // namespace mylib::flow
