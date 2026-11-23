#include "daal.h"
#include <cstring>
#include <iostream>

using namespace daal;
using namespace daal::algorithms;
using namespace daal::data_management;
using namespace daal::services;

static int releases = 0;
struct CountRelease {
    void operator()(const void *p) const {
        ++releases;
        delete static_cast<const double *>(p);
    }
};

template <typename Result, typename Slot>
static int table_contract(Slot slot) {
    NumericTablePtr retained;
    {
        SharedPtr<double> data(new double(17.25), CountRelease{});
        auto table = HomogenNumericTable<double>::create(data, 1, 1);
        Result result;
        result.set(slot, table);
        retained = result.get(slot);
        if (!retained || retained.get() != table.get()) return 10;
    }
    std::cout << "after_owner=" << releases << '\n';
    if (releases != 0) return 11;  // Check before dereferencing the survivor.
    {
        BlockDescriptor<double> block;
        if (!retained->getBlockOfRows(0, 1, readOnly, block)) return 13;
        const double value = block.getBlockPtr()[0];
        if (!retained->releaseBlockOfRows(block) || value != 17.25) return 14;
    }
    retained.reset();
    std::cout << "after_reset=" << releases << '\n';
    return releases == 1 ? 0 : 12;
}

static int status_contract() {
    Status survivor;
    {
        covariance::Batch<> algorithm;  // Missing input gives a deterministic error.
        auto result = algorithm.computeNoThrow();
        if (result.ok()) return 20;
        survivor = result;
        result.clear();
    }
    if (survivor.ok() || !survivor.getDescription()[0]) return 21;
    survivor.clear();
    if (!survivor.ok()) return 22;
    double values[] = {1, 2, 3, 4};
    covariance::Batch<> valid;
    auto input = HomogenNumericTable<double>::create(values, 2, 2);
    valid.input.set(covariance::data, input);
    return valid.computeNoThrow().ok() ? 0 : 23;
}

static int statistics_contract() {
    auto table = HomogenNumericTable<double>::create(3, 2, NumericTable::doAllocate);
    if (!table) return 32;
    auto status = table->NumericTable::allocateBasicStatistics();
    if (!status.ok()) return 30;
    for (auto id : {NumericTable::minimum, NumericTable::maximum, NumericTable::sum, NumericTable::sumSquares}) {
        auto statistic = table->basicStatistics.get(id);
        if (!statistic || statistic->getNumberOfRows() != 1 || statistic->getNumberOfColumns() != 3) return 31;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) return 64;
#define CHECK_GETTER(name, type, slot) if (!std::strcmp(argv[1], name)) return table_contract<type>(slot);
#include "return_getters.inc"
#undef CHECK_GETTER
    if (!std::strcmp(argv[1], "statistics")) return statistics_contract();
    if (!std::strcmp(argv[1], "status")) return status_contract();
    return 64;
}
