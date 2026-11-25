// SPDX-License-Identifier: Apache-2.0
#include "data_management/data/data_serialize.h"
#include <cstdlib>
using namespace daal;
using namespace daal::data_management;
extern "C" SerializationIface* get_object();
#ifdef PROVIDER
struct Primary { virtual ~Primary() = default; int padding = 0; };
struct Impl : Primary, SerializationIface {
    int getSerializationTag() const override { return 19; }
#ifndef OMIT_SERIALIZE
    services::Status serializeImpl(daal::data_management::interface1::InputDataArchive*) override { std::_Exit(21); }
#endif
    services::Status deserializeImpl(const daal::data_management::interface1::OutputDataArchive*) override { std::_Exit(22); }
};
extern "C" SerializationIface* get_object() {
    static Impl value;
    auto* base = static_cast<SerializationIface*>(&value);
    if (static_cast<void*>(base) == static_cast<void*>(&value)) std::_Exit(63);
    return base;
}
#else
int main(int argc, char**) {
    auto* base = get_object();
    if (argc == 1) base->serializeImpl(nullptr);
    else base->deserializeImpl(nullptr);
    return 64;
}
#endif
