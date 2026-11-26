// SPDX-License-Identifier: Apache-2.0
#include "algorithms/engines/engine_types.h"
#include "algorithms/kernel_function/kernel_function_types.h"
#include "data_management/data/homogen_numeric_table.h"

using daal::services::Status;
namespace dm = daal::data_management;
namespace alg = daal::algorithms;

// Force concrete calls so the compiler materializes each method specialization.
#define BLOCK_PROBES(S, T) \
Status get_##S##_##T(dm::HomogenNumericTable<S>& table, dm::BlockDescriptor<T>& block) { \
    return table.template getTBlock<T>(0, 1, 1, block); } \
Status release_##S##_##T(dm::HomogenNumericTable<S>& table, dm::BlockDescriptor<T>& block) { \
    return table.template releaseTBlock<T>(block); } \
Status feature_##S##_##T(dm::HomogenNumericTable<S>& table, dm::BlockDescriptor<T>& block) { \
    return table.template getTFeature<T>(0, 0, 1, 1, block); } \
Status release_feature_##S##_##T(dm::HomogenNumericTable<S>& table, dm::BlockDescriptor<T>& block) { \
    return table.template releaseTFeature<T>(block); }

#define ALL_BLOCKS(S) BLOCK_PROBES(S, int) BLOCK_PROBES(S, float) BLOCK_PROBES(S, double)
ALL_BLOCKS(int)
ALL_BLOCKS(float)
ALL_BLOCKS(double)

#define ALLOCATE_PROBE(NS) \
Status allocate_##NS(alg::NS::Result& result, const alg::Input* input, const alg::Parameter* param) { \
    return result.allocate<float>(input, param, 0); }
ALLOCATE_PROBE(kernel_function)
ALLOCATE_PROBE(engines)
