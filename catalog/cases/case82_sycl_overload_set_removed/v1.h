// case82 v1 — library is built with DPC++ enabled, exposing both CPU
// and SYCL overloads for each algorithm entry point. Mirrors oneDAL's
// dual-overload pattern:
//
//   result_t compute(const descriptor& d, const table& t);
//   result_t compute(sycl::queue& q, const descriptor& d, const table& t);
//
// Build-time switching DPC++ off removes EVERY sycl::queue&-taking
// overload from the binary, which is mechanically N×func_removed but
// semantically one event ("GPU/SYCL surface withdrawn from this build").
#pragma once

// Lightweight stand-in for sycl::queue so the case can build without
// DPC++ being installed. The detector only needs the parameter type to
// be named in a way that resembles a SYCL queue.
namespace sycl { class queue { public: int id = 0; }; }

namespace mylib {

struct descriptor { int n = 0; };
struct table      { int rows = 0; };
struct result_t   { int code = 0; };

// CPU entry points
result_t compute    (const descriptor& d, const table& t);
result_t train      (const descriptor& d, const table& t);
result_t infer      (const descriptor& d, const table& t);
result_t finalize   (const descriptor& d, const table& t);

// SYCL entry points (present in DPC++ build)
result_t compute    (sycl::queue& q, const descriptor& d, const table& t);
result_t train      (sycl::queue& q, const descriptor& d, const table& t);
result_t infer      (sycl::queue& q, const descriptor& d, const table& t);
result_t finalize   (sycl::queue& q, const descriptor& d, const table& t);

}  // namespace mylib
