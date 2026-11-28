// SPDX-License-Identifier: Apache-2.0
#include <oneapi/dal/algo/hdbscan.hpp>

// Resolve the SDK template's public setters in the shipped shared library.
extern template class oneapi::dal::hdbscan::v1::compute_result<>;

#include "hdbscan_result.cpp"
