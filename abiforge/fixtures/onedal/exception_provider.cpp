// SPDX-License-Identifier: Apache-2.0
#include "oneapi/dal/exceptions.hpp"

using namespace oneapi::dal;

extern "C" void raise_exception(int index) {
    switch (index) {
        case 0: throw invalid_argument("oneDAL ABI");
        case 1: throw uninitialized_optional_result("oneDAL ABI");
        case 2: throw domain_error("oneDAL ABI");
        case 3: throw out_of_range("oneDAL ABI");
        case 4: throw unimplemented("oneDAL ABI");
        case 5: throw unsupported_device("oneDAL ABI");
        case 6: throw range_error("oneDAL ABI");
        case 7: throw internal_error("oneDAL ABI");
        case 8: throw system_error(std::make_error_code(std::errc::invalid_argument), "oneDAL ABI");
        case 9: throw host_bad_alloc();
        case 10: throw device_bad_alloc();
    }
}
