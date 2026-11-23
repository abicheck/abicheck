#include "daal.h"
using namespace daal::services;
using namespace daal::data_management;

int main() {
    for (int i = 0; i < 256; ++i) {
        Status survivor;
        {
            Status original(ErrorNullByteInjection);
            Status copy(original);
            if (copy.ok() || !copy.getDescription()[0]) return 11;
            survivor = copy;
            if (survivor.ok() || !survivor.getDescription()[0]) return 12;
        }
        if (survivor.ok() || !survivor.getDescription()[0]) return 13;
        survivor.clear();
        if (!survivor.ok()) return 3;

        int releases = 0;
        NumericTablePtr retained;
        {
            auto table = HomogenNumericTable<double>::create(2, 3, NumericTable::doAllocate);
            if (!table) return 4;
            table->getArray()[0] = 17.25;
            // Keep the backing storage alive until the observed owner releases it.
            NumericTablePtr owner(table.get(), [table, &releases](const void*) { ++releases; });
            retained = owner;
        }
        if (releases != 0) return 14;
        if (retained->getNumberOfRows() != 3 || retained->getNumberOfColumns() != 2) return 5;
        BlockDescriptor<double> block;
        if (!retained->getBlockOfRows(0, 1, readOnly, block)) return 6;
        if (block.getBlockPtr()[0] != 17.25) return 7;
        if (!retained->releaseBlockOfRows(block)) return 8;
        retained.reset();
        if (releases != 1) return 15;
    }
}
