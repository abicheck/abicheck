// SPDX-License-Identifier: Apache-2.0
#include "pending.hpp"
#include <atomic>
#include <iostream>
#ifdef WITH_ONEDAL
#include <oneapi/dal/array.hpp>
#endif

int main() {
    sycl::queue queue(sycl::cpu_selector_v);
    if (!queue.get_device().has(sycl::aspect::usm_shared_allocations)) return 77;
    auto* data = sycl::malloc_shared<int>(1, queue);
    if (!data) return 1;
    *data = 42;
    std::atomic<int> released{0};
    auto release = [&](const int* ptr) {
        sycl::free(const_cast<int*>(ptr), queue);
        ++released;
    };
#ifdef WITH_ONEDAL
    using Array = oneapi::dal::array<int>;
    auto buffer = std::shared_ptr<int>(std::make_shared<Array>(
        queue, data, 1, release, std::vector<sycl::event>{}), data);
#else
    auto buffer = std::shared_ptr<int>(data, release);
#endif
    std::promise<void> resume;
    int before, result = 0;
    {
        auto done = submit_read(queue, buffer, resume.get_future().share(), result);
        buffer.reset();
        before = released.load();
        resume.set_value();
        done.wait_and_throw();
    }
    queue.wait_and_throw();
    std::cout << before << ' ' << result << ' ' << released << '\n';
    return before == 0 && result == 42 && released == 1 ? 0 : 2;
}
