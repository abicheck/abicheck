// SPDX-License-Identifier: Apache-2.0
#include <cstdint>
#include <string_view>
#include <type_traits>
#include <oneapi/dal/algo/logistic_regression.hpp>
#include <oneapi/dal/exceptions.hpp>
#include <oneapi/dal/table/csr.hpp>
#include <oneapi/dal/table/homogen.hpp>

namespace dal = oneapi::dal;
namespace lr = dal::logistic_regression;

template <typename Float, typename Method>
bool cpu_training_unimplemented(const dal::table& features, const dal::table& labels) {
    try {
        dal::train(lr::descriptor<Float, Method>{}, features, labels);
    } catch (const dal::unimplemented& error) {
        if constexpr (std::is_same_v<Method, lr::method::sparse>) {
            return std::string_view(error.what()) == "LogisticRegression does not have sparsity support for CPU";
        }
        return std::string_view(error.what()) == "LogisticRegression is not implemented for CPU";
    }
    return false;
}

template <typename Float>
bool check_methods() {
    const Float features[] = {-2, -1, 1, 2};
    const int responses[] = {0, 0, 1, 1};
    const std::int64_t columns[] = {1, 1, 1, 1};
    const std::int64_t offsets[] = {1, 2, 3, 4, 5};
    const auto labels = dal::homogen_table::wrap(responses, 4, 1);
    const auto dense = dal::homogen_table::wrap(features, 4, 1);
    const auto sparse = dal::csr_table::wrap(features, columns, offsets, 4, 1);
    return cpu_training_unimplemented<Float, lr::method::dense_batch>(dense, labels) &&
           cpu_training_unimplemented<Float, lr::method::sparse>(sparse, labels);
}

int main() {
    return check_methods<float>() && check_methods<double>() ? 0 : 1;
}
