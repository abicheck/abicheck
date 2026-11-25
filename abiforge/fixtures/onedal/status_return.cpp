#include "daal.h"
#include <cstdlib>
#include <cstring>

using namespace daal;
using namespace daal::algorithms;
using namespace daal::data_management;
using daal::services::Status;

extern "C" void* receiver;
#ifdef ONEDAL_OBSERVER
Status observe_table(NumericTable* self)
    asm("_ZN4daal15data_management10interface112NumericTable23allocateBasicStatisticsEv");
Status observe_table(NumericTable* self) { std::_Exit(self == receiver ? 0 : 42); }
Status observe_compute(AlgorithmImpl<batch>* self)
    asm("_ZN4daal10algorithms10interface113AlgorithmImplILNS_11ComputeModeE1EE14computeNoThrowEv");
Status observe_compute(AlgorithmImpl<batch>* self) { std::_Exit(self == receiver ? 0 : 42); }
Status observe_check(const covariance::Input* self, const Parameter*, int)
    asm("_ZNK4daal10algorithms10covariance10interface15Input5checkEPKNS0_10interface19ParameterEi");
Status observe_check(const covariance::Input* self, const Parameter*, int) { std::_Exit(self == receiver ? 0 : 42); }
#else
__attribute__((noinline)) Status check_input(const Input& input) { return input.check(nullptr, 0); }
void* receiver;
int main(int argc, char** argv) {
    if (argc != 2) return 64;
    if (!std::strcmp(argv[1], "table")) {
        auto table = HomogenNumericTable<double>::create(2, 2, NumericTable::doAllocate);
        receiver = static_cast<NumericTable*>(table.get());
        return table->NumericTable::allocateBasicStatistics().ok() ? 0 : 1;
    }
    if (!std::strcmp(argv[1], "virtual")) {
        covariance::Input input;
        receiver = &input;
        return check_input(input).ok() ? 1 : 0;
    }
    covariance::Batch<> algorithm;
    receiver = static_cast<AlgorithmImpl<batch>*>(&algorithm);
    return algorithm.computeNoThrow().ok() ? 1 : 0;
}
#endif
