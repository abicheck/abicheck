// SPDX-License-Identifier: Apache-2.0
#include <cxxabi.h>
#include <cstring>
#include <dlfcn.h>
#include <typeinfo>

int main() {
    const auto* info = static_cast<const std::type_info*>(
        dlsym(RTLD_DEFAULT, "_ZTIN4daal10algorithms6dtrees8internal4TreeE"));
    if (!info || std::strcmp(info->name(), "N4daal10algorithms6dtrees8internal4TreeE") != 0) {
        return 1;
    }
    const auto* tree = dynamic_cast<const __cxxabiv1::__si_class_type_info*>(info);
    if (!tree) return 2;
    const auto* base = tree->__base_type;
    if (!base || base != dlsym(RTLD_DEFAULT, "_ZTIN4daal4BaseE")) return 3;
    if (std::strcmp(base->name(), "N4daal4BaseE") != 0) return 4;
    return typeid(*base) == typeid(__cxxabiv1::__class_type_info) ? 0 : 5;
}
