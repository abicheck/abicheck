// SPDX-License-Identifier: Apache-2.0
#include <cmath>
#include <oneapi/dal/algo/decision_forest.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>

namespace dal = oneapi::dal;
namespace df = dal::decision_forest;

template <typename Float, typename Method, typename Task>
bool predictions_match() {
    const Float data[] = {-4, -3, -2, -1, 1, 2, 3, 4};
    const Float labels[] = {0, 0, 0, 0, 1, 1, 1, 1};
    const Float query[] = {-3.5, 3.5};
    const auto table = [](const Float* values, int rows) {
        return dal::homogen_table::wrap(values, rows, 1);
    };
    df::descriptor<Float, Method, Task> descriptor;
    descriptor.set_tree_count(3).set_bootstrap(false).set_min_observations_in_leaf_node(1);
    descriptor.set_min_bin_size(1);
    const auto trained = dal::train(descriptor, table(data, 8), table(labels, 8));
    const auto prediction = dal::infer(descriptor, trained.get_model(), table(query, 2));
    const auto& responses = prediction.get_responses();
    if (responses.get_row_count() != 2 || responses.get_column_count() != 1) {
        return false;
    }
    const auto values = dal::row_accessor<const Float>(responses).pull();
    return std::abs(values[0]) < 1e-5 && std::abs(values[1] - 1) < 1e-5;
}

template <typename Float, typename Method>
bool check_tasks() {
    return predictions_match<Float, Method, df::task::classification>() &&
           predictions_match<Float, Method, df::task::regression>();
}

int main() {
    return check_tasks<float, df::method::dense>() && check_tasks<double, df::method::dense>() &&
           check_tasks<float, df::method::hist>() && check_tasks<double, df::method::hist>() ? 0 : 1;
}
