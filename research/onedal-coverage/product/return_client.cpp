#include "daal.h"
#include <cstring>
#include <iostream>
#include <type_traits>

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

template <typename Result, bool indexed, typename Slot>
static int table_contract(Slot slot) {
    NumericTablePtr retained;
    {
        SharedPtr<double> data(new double(17.25), CountRelease{});
        auto table = HomogenNumericTable<double>::create(data, 1, 1);
        auto result = [&]() {
            if constexpr (std::is_same_v<Result, adaboost::Model> || std::is_same_v<Result, brownboost::Model>) {
                struct ModelFixture : Result { void put(const NumericTablePtr& p) { this->_alpha = p; } };
                return ModelFixture();
            } else if constexpr (std::is_same_v<Result, implicit_als::training::init::PartialResultBase> ||
                          std::is_same_v<Result, linear_model::prediction::Result> ||
                          std::is_same_v<Result, linear_model::prediction::Input> ||
                          std::is_same_v<Result, linear_model::training::Input> ||
                          std::is_same_v<Result, regression::prediction::Input>)
                return Result(static_cast<size_t>(slot) + 1);
            else
                return Result();
        }();
        if constexpr (std::is_same_v<Result, optimization_solver::iterative_solver::Result> && std::is_same_v<Slot, optimization_solver::iterative_solver::OptionalDataId>) {
            result.set(optimization_solver::iterative_solver::optionalResult,
                       OptionalArgumentPtr(new OptionalArgument(static_cast<size_t>(slot) + 1)));
        }
        if constexpr (std::is_same_v<Result, stump::classification::training::Result>) {
            // This result starts with only the model slot in both tested SDKs.
            while (result.size() <= static_cast<size_t>(slot))
                result << SerializationIfacePtr();
        }
        if constexpr (std::is_same_v<Slot, optimization_solver::iterative_solver::OptionalDataId> &&
                      std::is_same_v<Result, optimization_solver::iterative_solver::Input>) {
            result.set(optimization_solver::iterative_solver::optionalArgument,
                       OptionalArgumentPtr(new OptionalArgument(static_cast<size_t>(slot) + 1)));
        }
        if constexpr (std::is_same_v<Slot, optimization_solver::mse::OptionalDataId>) {
            while (result.size() <= optimization_solver::mse::optionalArgument)
                result << SerializationIfacePtr();
            result.set(optimization_solver::mse::optionalArgument,
                       OptionalArgumentPtr(new OptionalArgument(static_cast<size_t>(slot) + 1)));
        }
        if constexpr (std::is_same_v<Result, adaboost::Model> || std::is_same_v<Result, brownboost::Model>) {
            result.put(table);
            retained = result.getAlpha();
        } else if constexpr (std::is_same_v<Slot, implicit_als::training::Step3LocalCollectionInputId>) {
            KeyValueDataCollectionPtr collection(new KeyValueDataCollection());
            (*collection)[0] = table;
            result.set(slot, collection);
            retained = result.getOutBlockIndices(0);
        } else if constexpr (std::is_same_v<Slot, pca::transform::TransformDataInputId>) {
            result.set(slot, KeyValueDataCollectionPtr(new KeyValueDataCollection()));
            result.set(slot, pca::transform::mean, table);
            retained = result.get(slot, pca::transform::mean);
        } else if constexpr (std::is_same_v<Slot, kmeans::init::DistributedStep3MasterPlusPlusPartialResultId>) {
            result.add(slot, 0, table);
            retained = result.get(slot, 0);
        } else if constexpr (std::is_same_v<Slot, implicit_als::training::init::PartialResultCollectionId> ||
                             std::is_same_v<Slot, implicit_als::training::init::PartialResultBaseId>) {
            KeyValueDataCollectionPtr collection(new KeyValueDataCollection());
            (*collection)[0] = table;
            result.set(slot, collection);
            retained = result.get(slot, 0);
        } else if constexpr (indexed) {
            DataCollectionPtr collection(new DataCollection());
            collection->push_back(table);
            result.set(slot, collection);
            retained = result.get(slot, 0);
        } else {
            result.set(slot, table);
            retained = result.get(slot);
        }
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
#define CHECK_GETTER(name, type, slot, indexed) if (!std::strcmp(argv[1], name)) return table_contract<type, indexed>(slot);
#include "return_getters.inc"
#undef CHECK_GETTER
    if (!std::strcmp(argv[1], "statistics")) return statistics_contract();
    if (!std::strcmp(argv[1], "status")) return status_contract();
    return 64;
}
