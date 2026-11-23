#include "daal.h"
#include <cstdlib>
#include <dlfcn.h>
#include <new>

using daal::services::Status;
#ifdef COPY
#define ENTRY _ZN4daal8services10interface16StatusC1ERKS2_
using Result = void;
#else
#define ENTRY _ZN4daal8services10interface16StatusaSERKS2_
using Result = Status*;
#endif
#define STRINGIFY_IMPL(x) #x
#define STRINGIFY(x) STRINGIFY_IMPL(x)

extern "C" Result ENTRY(Status* target, const Status* source) {
#ifdef FORWARD
    auto original = reinterpret_cast<Result (*)(Status*, const Status*)>(
        dlsym(RTLD_NEXT, STRINGIFY(ENTRY)));
    if (!original) std::abort();
    return original(target, source);
#elif defined(COPY)
    new (target) Status();
#else
    return target;
#endif
}
