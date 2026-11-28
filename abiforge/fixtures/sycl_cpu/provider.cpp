// SPDX-License-Identifier: Apache-2.0
#include "pending.hpp"

sycl::event submit_read(sycl::queue& queue, std::shared_ptr<int> buffer,
                        std::shared_future<void> gate, int& result) {
    std::weak_ptr<int> observer = buffer;
#ifdef DROP_OWNER
    buffer.reset();
#endif
    return queue.submit([&](sycl::handler& handler) {
        handler.host_task([buffer, observer, gate, &result] {
            gate.wait();
            auto readable = observer.lock();
            result = readable ? *readable : -1;
        });
    });
}
