// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <oneapi/dal/table/common.hpp>

struct sparse_request {
    unsigned char tag;
    oneapi::dal::sparse_indexing indexing;
};

extern "C" void request_zero_based(sparse_request* request);
