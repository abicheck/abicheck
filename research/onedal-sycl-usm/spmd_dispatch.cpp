#include <cmath>
#include <iostream>
#include <oneapi/dal/algo/covariance.hpp>
#include <oneapi/dal/algo/linear_regression.hpp>
#include <oneapi/dal/spmd/mpi/communicator.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>
#include <string>

namespace dal = oneapi::dal;

int run(const std::string &operation) {
  sycl::queue q;
  auto comm =
      dal::preview::spmd::make_communicator<dal::preview::spmd::backend::mpi>(
          q);
  const float x[] = {1, 2, 3, 4};
  const float y[] = {3, 5, 7, 9};
  auto data = dal::homogen_table::wrap(x, 4, 1);
  auto labels = dal::homogen_table::wrap(y, 4, 1);
  dal::covariance::descriptor<float> cov;
  if (comm.get_rank_count() != 2)
    return 1;
  if (operation == "compute" || operation == "finalize_compute") {
    auto result = operation == "compute"
                      ? dal::preview::compute(comm, cov, data)
                      : dal::preview::finalize_compute(
                            comm, cov, dal::partial_compute(q, cov, data));
    auto m = dal::row_accessor<const float>(result.get_cov_matrix()).pull();
    return !(m.get_count() == 1 && std::abs(m[0] - 10.0f / 7) < 1e-4f);
  }
  dal::linear_regression::descriptor<float> lr;
#ifdef ONEDAL_PROBE_SPMD_PARTIAL_TRAIN
  if (operation == "partial_train") {
    auto state = dal::preview::partial_train(comm, lr, data, labels);
    return !state.get_partial_xtx().has_data();
  }
#endif
  auto fit = dal::train(q, lr, data, labels);
  if (operation == "train")
    fit = dal::preview::train(comm, lr, data, labels);
  else if (operation == "finalize_train")
    fit = dal::preview::finalize_train(comm, lr,
                                       dal::partial_train(q, lr, data, labels));
  else if (operation != "infer")
    return 2;
  auto result = operation == "infer"
                    ? dal::preview::infer(comm, lr, data, fit.get_model())
                    : dal::infer(q, lr, data, fit.get_model());
  auto pred = dal::row_accessor<const float>(result.get_responses()).pull();
  if (pred.get_count() != 4)
    return 1;
  for (int i = 0; i < 4; ++i)
    if (!(std::abs(pred[i] - y[i]) < 1e-4f))
      return 1;
  auto device = q.get_device();
  std::cout << device.get_info<sycl::info::device::name>() << " / "
            << device.get_info<sycl::info::device::driver_version>() << '\n';
  return 0;
}

int main(int argc, char **argv) {
  MPI_Init(&argc, &argv);
  int rc = 2;
  try {
    if (argc == 2)
      rc = run(argv[1]);
  } catch (const dal::unimplemented &e) {
    std::cerr << e.what() << '\n';
    rc = 77;
  }
  if (rc && rc != 77)
    MPI_Abort(MPI_COMM_WORLD, rc);
  MPI_Finalize();
  return rc;
}
