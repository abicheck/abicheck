// Test-only SYCL stub: forward declarations so castxml can parse the
// SYCL_LANGUAGE_VERSION branch of a header without a SYCL front end, with
// the DPC++ nesting (sycl::_V1) so mangled names match the exports.
// Never compiled into a shipped library; abicheck does not bundle it.
#pragma once
namespace sycl { inline namespace _V1 { class queue; class event; class device; class context; } }
