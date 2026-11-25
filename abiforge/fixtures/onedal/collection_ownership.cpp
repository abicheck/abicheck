// SPDX-License-Identifier: Apache-2.0
#include "daal.h"
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>

using namespace daal;
using namespace daal::algorithms;
using namespace daal::data_management;

template<class P, class Owner, class Slot>
P forward_get(const char* symbol, const Owner* self, Slot slot) {
    using Get = P (*)(const Owner*, Slot);
    auto next = reinterpret_cast<Get>(dlsym(RTLD_NEXT, symbol));
    if (!next) std::abort();
    auto result = next(self, slot);
    const char* mode = std::getenv("ONEDAL_OWNERSHIP");
    if (std::strcmp(mode, "borrow") == 0) return P(P(), result.get());
    if (std::strcmp(mode, "extra_owner") == 0) {
        static P extra;
        extra = result;
    }
    return result;
}

#define COV_GET "_ZNK4daal10algorithms10covariance10interface116DistributedInputILNS_11ComputeStepE1EE3getENS1_13MasterInputIdE"
#define PCA_GET "_ZNK4daal10algorithms3pca9transform10interface15Input3getENS2_20TransformDataInputIdE"
using CovInput = covariance::DistributedInput<step2Master>;
DataCollectionPtr cov_get(const CovInput*, covariance::MasterInputId) asm(COV_GET);
DataCollectionPtr cov_get(const CovInput* self, covariance::MasterInputId slot) {
    return forward_get<DataCollectionPtr>(COV_GET, self, slot);
}
KeyValueDataCollectionPtr pca_get(const pca::transform::Input*, pca::transform::TransformDataInputId) asm(PCA_GET);
KeyValueDataCollectionPtr pca_get(const pca::transform::Input* self, pca::transform::TransformDataInputId slot) {
    return forward_get<KeyValueDataCollectionPtr>(PCA_GET, self, slot);
}
