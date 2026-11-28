// SPDX-License-Identifier: Apache-2.0
#include <oneapi/dal/detail/parameters/system_parameters.hpp>
#include <iostream>

int main() {
    const oneapi::dal::detail::system_parameters parameters;
    const auto threads = parameters.get_max_number_of_threads();
    const auto extension = parameters.get_top_enabled_cpu_extension();
    const auto description = parameters.dump();
    std::cout << threads << '\n' << static_cast<unsigned long long>(extension)
              << '\n' << description << '\n';
    return threads == 0 || description.empty();
}
