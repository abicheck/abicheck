// SPDX-License-Identifier: Apache-2.0
#include <initializer_list>
#include <oneapi/dal/algo/covariance.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>

namespace dal = oneapi::dal;

template <typename Float>
bool sample_covariance_matches() {
    const Float data[] = {1, 2, 2, 4, 3, 6};
    const auto input = dal::homogen_table::wrap(data, 3, 2);
    dal::covariance::descriptor<Float> descriptor;
    descriptor.set_bias(false).set_result_options(dal::covariance::result_options::cov_matrix);
    const auto result = dal::compute(descriptor, input);
    const auto& matrix = result.get_cov_matrix();
    if (matrix.get_row_count() != 2 || matrix.get_column_count() != 2) {
        return false;
    }
    const auto values = dal::row_accessor<const Float>(matrix).pull();
    if (values[0] != 1 || values[1] != 2 || values[2] != 2 || values[3] != 4) {
        return false;
    }
    using result_type = dal::covariance::compute_result<>;
    for (auto getter : {&result_type::get_cor_matrix, &result_type::get_means}) {
        try {
            (result.*getter)();
            return false;
        }
        catch (const dal::domain_error&) {
            // Both outputs were disabled by the client's descriptor.
        }
    }
    return true;
}

int main() {
    return sample_covariance_matches<float>() && sample_covariance_matches<double>() ? 0 : 1;
}
