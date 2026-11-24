#include <cmath>
#include <iostream>
#include <oneapi/dal/algo/covariance.hpp>
#include <oneapi/dal/algo/linear_regression.hpp>
#include <oneapi/dal/spmd/mpi/communicator.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>
namespace d = oneapi::dal;
namespace s = d::preview::spmd;

int run() {
  sycl::queue q;
  auto comm = s::make_communicator<s::backend::mpi>(q);
  if (comm.get_rank_count() != 2 || comm.get_queue() != q)
    return 1;
  auto storage = d::array<int>::zeros(q, 4, sycl::usm::alloc::shared);
  auto p = storage.get_mutable_data();
  int rank = comm.get_rank();
  p[0] = rank + 1;
  comm.allreduce(q, p, p + 1, 1, s::reduce_op::sum, {}).wait();
  if (p[1] != 3)
    return 2;
  std::int64_t counts[]{1, 1}, offsets[]{0, 1};
  comm.allgatherv(q, p, 1, p + 2, counts, offsets).wait();
  if (p[2] != 1 || p[3] != 2)
    return 3;
  comm.sendrecv_replace(q, p, 1, 1 - rank, 1 - rank).wait();
  if (p[0] != 2 - rank)
    return 4;
  comm.bcast(q, p, 1, {}, 0).wait();
  if (p[0] != 2)
    return 5;
  auto bytes = reinterpret_cast<d::byte_t *>(p);
  auto out = reinterpret_cast<d::byte_t *>(p + 1);
  auto gathered = reinterpret_cast<d::byte_t *>(p + 2);
  auto dtype = d::data_type::int32;
  p[0] = rank + 3;
  comm.allreduce(q, bytes, out, 1, dtype, s::reduce_op::sum, {}).wait();
  if (p[1] != 7)
    return 6;
  comm.allgatherv(q, bytes, 1, gathered, counts, offsets, dtype, {}).wait();
  if (p[2] != 3 || p[3] != 4)
    return 7;
  comm.sendrecv_replace(q, bytes, 1, dtype, 1 - rank, 1 - rank, {}).wait();
  if (p[0] != 4 - rank)
    return 8;
  comm.bcast(q, bytes, 1, dtype, {}, 0).wait();
  return p[0] != 4;
}
int main(int argc, char **argv) {
  MPI_Init(&argc, &argv);
  int result = run();
  if (result)
    MPI_Abort(MPI_COMM_WORLD, result);
  MPI_Finalize();
  return result;
}
