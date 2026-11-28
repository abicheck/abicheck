// SPDX-License-Identifier: Apache-2.0
#include <cmath>
#include <oneapi/dal/algo/linear_regression.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>

namespace dal = oneapi::dal;

template <typename Float>
bool predictions_match() {
    const Float features[] = {0, 1, 2, 3};
    const Float responses[] = {1, 3, 5, 7};
    const Float query[] = {-1, 4};
    const auto table = [](const Float* data, int rows) {
        return dal::homogen_table::wrap(data, rows, 1);
    };
    dal::linear_regression::descriptor<Float> descriptor;
    const auto check = [&](const auto& trained) {
        const auto result = dal::infer(descriptor, table(query, 2), trained.get_model());
        const auto& prediction = result.get_responses();
        if (prediction.get_row_count() != 2 || prediction.get_column_count() != 1) {
            return false;
        }
        const auto values = dal::row_accessor<const Float>(prediction).pull();
        return std::abs(values[0] + 1) < 1e-4 && std::abs(values[1] - 9) < 1e-4;
    };
    const auto batch = dal::train(descriptor, table(features, 4), table(responses, 4));
    const auto first = dal::partial_train(descriptor, table(features, 2), table(responses, 2));
    const auto second = dal::partial_train(descriptor, first, table(features + 2, 2),
                                           table(responses + 2, 2));
    return check(batch) && check(dal::finalize_train(descriptor, second));
}

int main() {
    return predictions_match<float>() && predictions_match<double>() ? 0 : 1;
}
