abicheck_add_case(case126_sycl_device_impl_ptr
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    # Hide inline std::shared_ptr members so the diff shows the public ABI break
    # (the sycl::device size change) rather than libstdc++ template noise — the
    # same -fvisibility discipline real libsycl is built with.
    V1_COMPILE_OPTIONS -fvisibility=hidden -fvisibility-inlines-hidden
    V2_COMPILE_OPTIONS -fvisibility=hidden -fvisibility-inlines-hidden
    APP_SOURCES app.cpp
    PLATFORMS linux
)
