// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <future>
#include <memory>
#include <sycl/sycl.hpp>

sycl::event submit_read(sycl::queue& queue, std::shared_ptr<int> buffer,
                        std::shared_future<void> gate, int& result);
