// SPDX-License-Identifier: Apache-2.0
#include <string_view>
#include <mpi.h>
#include <oneapi/dal/algo/hdbscan.hpp>
#include <oneapi/dal/spmd/mpi/communicator.hpp>
#include <oneapi/dal/table/homogen.hpp>

namespace dal = oneapi::dal;
namespace hdb = dal::hdbscan;
namespace spmd = dal::preview::spmd;

template <typename Float, typename Method, typename Comm>
bool check(Comm& comm) {
    const Float points[] = {0, 0, 1, 1, 2, 2, 3, 3};
    try {
        dal::preview::compute(comm, hdb::descriptor<Float, Method>(2, 2),
                                            dal::homogen_table::wrap(points, 4, 2));
        return false;
    }
    catch (const dal::unimplemented& error) {
        return std::string_view(error.what()) == "SPMD version of the algorithm is not implemented";
    }
}

int main(int argc, char** argv) {
    MPI_Init(&argc, &argv);
    bool passed = false;
    {
        auto comm = spmd::make_communicator<spmd::backend::mpi>();
        passed = comm.get_rank_count() == 2 &&
                 check<float, hdb::method::brute_force>(comm) && check<double, hdb::method::brute_force>(comm) &&
                 check<float, hdb::method::kd_tree>(comm) && check<double, hdb::method::kd_tree>(comm) &&
                 check<float, hdb::method::ball_tree>(comm) && check<double, hdb::method::ball_tree>(comm);
    }
    MPI_Finalize();
    return passed ? 0 : 1;
}
