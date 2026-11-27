// SPDX-License-Identifier: Apache-2.0
#include "oneapi/dal/exceptions.hpp"
#include <cstring>

extern "C" void raise_exception(int index);

int main() {
    for (int index = 0; index < 11; ++index) {
        try {
            raise_exception(index);
            return 1;
        } catch (const oneapi::dal::exception& error) {
            const auto* standard = dynamic_cast<const std::exception*>(&error);
            if (!standard || !error.what() || !*error.what() ||
                std::strcmp(error.what(), standard->what()) != 0) return 2;
            if (index < 9 && !std::strstr(error.what(), "oneDAL ABI")) return 3;
        }
        try {
            raise_exception(index);
            return 4;
        } catch (const std::exception& error) {
            const auto* dal = dynamic_cast<const oneapi::dal::exception*>(&error);
            if (!dal || !error.what() || std::strcmp(error.what(), dal->what()) != 0) return 5;
        }
    }
}
