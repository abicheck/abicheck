#include <oneapi/dal/chunked_array.hpp>
#include <oneapi/dal/io/csv.hpp>
#include <oneapi/dal/read.hpp>
#include <oneapi/dal/table/csr.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>
namespace d = oneapi::dal;
int main(int argc, char **argv) {
  if (argc != 2)
    return 1; // CSV containing one row: 7,8
  sycl::queue q;
  auto usm = sycl::usm::alloc::shared;
  using A = d::array<float>;
  auto owner = std::shared_ptr<float>(sycl::malloc_shared<float>(1, q),
                                      [&q](float *p) { sycl::free(p, q); });
  *owner = 7;
  auto p = owner.get();
  const float *cp = p;
  std::shared_ptr<const float> cowner = owner;
  int released = 0;
  auto observe = [&released](const auto *) { ++released; };
  {
    A a(q, p, 1, observe, {}), b(q, cp, 1, observe, {}), c(q, owner, 1, {}),
        e(q, cowner, 1, {});
    if (a[0] != 7 || b[0] != 7 || c[0] != 7 || e[0] != 7 || c.get_queue() != q)
      return 2;
    a.reset(q, cp, 1, observe, {});
    b.reset(q, p, 1, observe, {});
#ifdef ONEDAL_PROBE_DEPRECATED_MUTABLE
    a.need_mutable_data(q, usm);
#endif
    a.need_mutable_data().get_mutable_data()[0] = 9;
    if (*p != 7 || a[0] != 9 || b[0] != 7)
      return 3;
    b.reset(q, 3, usm);
    if (b.get_count() != 3 || A::wrap(p, 1, {})[0] != 7)
      return 4;
    d::chunked_array<float> chunks(c, e);
    d::detail::data_parallel_policy policy(q);
    if (chunks.flatten(policy, {})[1] != 7 ||
        chunks.get_data(policy, {})[0] != p)
      return 5;
    d::homogen_table dense(q, cp, 1, 1, observe, {});
    auto idx = d::array<std::int64_t>::full(q, 3, std::int64_t{1}, usm);
    idx.get_mutable_data()[2] = 2;
    d::csr_table csr(q, cp, idx.get_data(), idx.get_data() + 1, 1, 1, observe,
                     observe, observe, d::sparse_indexing::one_based, {});
    if (csr.get_non_zero_count() != 1 || dense.get_data<float>()[0] != 7)
      return 6;
  }
  if (released != 8)
    return 7;
  auto table = d::read<d::table>(q, d::csv::data_source<float>(argv[1]));
  auto data = d::row_accessor<const float>(table).pull();
  return !(data.get_count() == 2 && data[0] == 7 && data[1] == 8);
}
