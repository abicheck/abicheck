// SPDX-License-Identifier: Apache-2.0
#include <cstdint>
#include <cxxabi.h>
#include <dlfcn.h>
#include <typeinfo>

#include <services/daal_shared_ptr.h>

namespace sdk = daal::services::interface1;

class Counter final : public sdk::RefCounter {
    void operator()(const void*) override {}
};

int main(int argc, char** argv) {
    if (argc != 2) return 1;
    void* core = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!core) return 2;
    const auto* info = static_cast<const std::type_info*>(
        dlsym(core, "_ZTIN4daal8services10interface110RefCounterE"));
    const auto* layout = dynamic_cast<const __cxxabiv1::__vmi_class_type_info*>(info);
    if (!layout || layout->__base_count != 1) return 3;
    const auto& base = layout->__base_info[0];
    if (!base.__is_public_p() || base.__is_virtual_p()) return 4;
    if (base.__base_type != dlsym(core, "_ZTIN4daal8services10interface16AtomicIiEE")) return 5;

    Counter object;
    const sdk::RefCounter* counter = &object;
    const sdk::Atomic<int>* atomic = counter;
    const auto offset = reinterpret_cast<std::uintptr_t>(atomic) -
                        reinterpret_cast<std::uintptr_t>(counter);
    return offset == 8 && base.__offset() == 8 ? 0 : 6;
}
