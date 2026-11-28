// SPDX-License-Identifier: Apache-2.0
#include <cmath>
#include <type_traits>
#include <oneapi/dal/algo/pca.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>

namespace dal = oneapi::dal;

template <typename Float>
bool eigenvalues_match() {
    const Float data[] = {1, 2, 2, 4, 3, 6, 4, 8};
    dal::pca::descriptor<Float, dal::pca::method::PCA_METHOD> descriptor;
    descriptor.set_component_count(2);
    descriptor.set_normalization_mode(dal::pca::normalization::zscore);
    const auto table = [](const Float* rows, int count) {
        return dal::homogen_table::wrap(rows, count, 2);
    };
    const auto check = [](const auto& trained) {
        const auto& eigenvalues = trained.get_eigenvalues();
        if (eigenvalues.get_row_count() != 1 || eigenvalues.get_column_count() != 2) {
            return false;
        }
        const auto values = dal::row_accessor<const Float>(eigenvalues).pull();
        return std::abs(values[0] - 2) < 1e-4 && std::abs(values[1]) < 1e-4;
    };
    if constexpr (std::is_same_v<dal::pca::method::PCA_METHOD, dal::pca::method::precomputed>) {
        const Float correlation[] = {1, 1, 1, 1};
        return check(dal::train(descriptor, table(correlation, 2)));
    }
    const auto batch = dal::train(descriptor, table(data, 4));
    if constexpr (std::is_same_v<dal::pca::method::PCA_METHOD, dal::pca::method::svd>) {
        return check(batch);
    }
    const auto first = dal::partial_train(descriptor, table(data, 2));
    const auto second = dal::partial_train(descriptor, first, table(data + 4, 2));
    return check(batch) && check(dal::finalize_train(descriptor, second));
}

int main() {
    return eigenvalues_match<float>() && eigenvalues_match<double>() ? 0 : 1;
}
