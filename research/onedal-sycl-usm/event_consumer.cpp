#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>
#include <iostream>
namespace dal = oneapi::dal;
int main() {
    sycl::queue consumer;
    sycl::queue producer(consumer.get_context(), consumer.get_device());
    for (auto kind : {sycl::usm::alloc::shared, sycl::usm::alloc::host, sycl::usm::alloc::device}) {
        const char* name = kind == sycl::usm::alloc::shared ? "shared" :
                           kind == sycl::usm::alloc::host ? "host" : "device";
        auto p = sycl::malloc<float>(256, producer, kind);
        if (!p) { std::cout << name << " unavailable\n"; continue; }
        {
            auto written = producer.parallel_for(sycl::range<1>(256), [=](sycl::id<1> i) {
                p[i] = static_cast<float>(i[0] + 1);
            });
            auto table = dal::homogen_table::wrap(consumer, p, 16, 16, {written});
            auto rows = dal::row_accessor<const float>(table).pull(consumer);
            float observed[256];
            consumer.memcpy(observed, rows.get_data(), sizeof(observed)).wait_and_throw();
            for (int i = 0; i < 256; ++i) if (observed[i] != i + 1) return 2;
            producer.wait_and_throw();
        }
        sycl::free(p, producer);
        std::cout << name << " 256/256\n";
    }
}
