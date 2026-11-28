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
    sdk::RefCounter* counter = &object;
    const sdk::Atomic<int>* atomic = counter;
    const auto offset = reinterpret_cast<std::uintptr_t>(atomic) -
                        reinterpret_cast<std::uintptr_t>(counter);
    if (offset != 8 || base.__offset() != 8) return 6;

    // Ask libstdc++ to match a RefCounter exception against its public base.
    // Both type descriptors come from the loaded core library.
    const std::type_info* catch_type = base.__base_type;
    void* adjusted = counter;
    if (!catch_type->__do_catch(info, &adjusted, 1) || adjusted != atomic) return 7;
    auto* caught = static_cast<sdk::Atomic<int>*>(adjusted);
    if (static_cast<sdk::RefCounter*>(caught) != counter) return 8;
    if (caught->get() != 1 || caught->inc() != 2 || counter->get() != 2) return 9;
    return counter->dec() == 1 && caught->get() == 1 ? 0 : 10;
}
