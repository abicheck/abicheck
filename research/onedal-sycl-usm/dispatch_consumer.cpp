#include <oneapi/dal/algo/covariance.hpp>
#include <oneapi/dal/algo/linear_regression.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>
#include <cmath>
#include <iostream>

namespace dal = oneapi::dal;

int main() {
    sycl::queue q;
    const float x[] = {1, 2, 3, 4};
    const float y[] = {3, 5, 7, 9};
    auto data = dal::homogen_table::wrap(x, 4, 1);
    auto labels = dal::homogen_table::wrap(y, 4, 1);
    dal::covariance::descriptor<float> cov;
    auto partial = dal::partial_compute(q, cov, data);
    auto online = dal::finalize_compute(q, cov, partial);
    auto batch = dal::compute(q, cov, data);
    auto values = [](const dal::table& t) {
        return dal::row_accessor<const float>(t).pull();
    };
    for (auto& result : {online, batch}) {
        auto matrix = values(result.get_cov_matrix());
        if (matrix.get_count() != 1 || !(std::abs(matrix[0] - 5.0f / 3) < 1e-4f)) return 1;
    }
    dal::linear_regression::descriptor<float> lr;
    auto fit = dal::train(q, lr, data, labels);
    auto state = dal::partial_train(q, lr, data, labels);
    auto complete = dal::finalize_train(q, lr, state);
    for (auto& result : {fit, complete}) {
        auto predictions = values(dal::infer(q, lr, data, result.get_model()).get_responses());
        if (predictions.get_count() != 4) return 2;
        for (int i = 0; i < 4; ++i) {
            if (!(std::abs(predictions[i] - y[i]) < 1e-4f)) return 2;
        }
    }
    auto device = q.get_device();
    std::cout << device.get_info<sycl::info::device::name>() << " / "
              << device.get_info<sycl::info::device::driver_version>() << '\n';
}
