// SPDX-License-Identifier: Apache-2.0
#include "daal.h"
#include <cstdio>
#include <cstring>

using namespace daal;
using namespace daal::algorithms;
using namespace daal::data_management;

namespace {
int destroyed = 0;
struct TrackedPartial : covariance::PartialResult {
    ~TrackedPartial() override { ++destroyed; }
};
struct TrackedMap : KeyValueDataCollection {
    ~TrackedMap() override { ++destroyed; }
};
}

int main(int argc, char** argv) {
    if (argc != 2) return 64;
    if (!std::strcmp(argv[1], "collection")) {
        DataCollectionPtr held;
        {
            covariance::DistributedInput<step2Master> owner;
            owner.add(covariance::partialResults, covariance::PartialResultPtr(new TrackedPartial));
            held = owner.get(covariance::partialResults);
        }
        if (destroyed || !held || held->size() != 1) return 1;
        held.reset();
        return destroyed == 1 ? 0 : 2;
    }
    if (!std::strcmp(argv[1], "key_value")) {
        KeyValueDataCollectionPtr held;
        {
            pca::transform::Input owner;
            KeyValueDataCollectionPtr value(new TrackedMap);
            owner.set(pca::transform::dataForTransform, value);
            held = owner.get(pca::transform::dataForTransform);
            if (held.get() != value.get()) return 3;
        }
        if (destroyed || !held || held->size() != 0) return 4;
        held.reset();
        return destroyed == 1 ? 0 : 5;
    }
    if (!std::strcmp(argv[1], "delete_base")) {
        covariance::PartialResult* base = new TrackedPartial;
        delete base;
        return destroyed == 1 ? 0 : 6;
    }
    if (!std::strcmp(argv[1], "variable")) {
        std::printf("%d\n", services::String::__DAAL_STR_MAX_SIZE);
        return 0;
    }
    return 64;
}
