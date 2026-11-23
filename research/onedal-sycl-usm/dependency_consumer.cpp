#include <oneapi/dal/table/homogen.hpp>
#include <chrono>
#include <condition_variable>
#include <iostream>
#include <mutex>

int main() {
    sycl::queue consumer;
    sycl::queue producer(consumer.get_context(), consumer.get_device());
    auto* data = sycl::malloc_shared<int>(1, consumer);
    if (!data) return 3;
    *data = 0;
    std::mutex mutex;
    std::condition_variable changed;
    bool observed = false;
    auto ready = producer.submit([&](sycl::handler& h) {
        h.host_task([&] {
            std::unique_lock lock(mutex);
            changed.wait_for(lock, std::chrono::seconds(2), [&] { return observed; });
            *data = 42;
        });
    });
    int value;
    {
        auto table = oneapi::dal::homogen_table::wrap(consumer, data, 1, 1, {ready});
        std::lock_guard lock(mutex);
        value = *table.get_data<int>();
        observed = true;
    }
    changed.notify_one();
    ready.wait_and_throw();
    sycl::free(data, consumer);
    std::cout << value << '\n';
    return value == 42 ? 0 : 2;
}
