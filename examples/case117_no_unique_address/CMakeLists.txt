abicheck_add_case(case117_no_unique_address
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    APP_SOURCES app.cpp
    PLATFORMS linux
)

# [[no_unique_address]] is a C++20 feature; the repo-wide default is C++17.
target_compile_features(case117_no_unique_address_v1 PRIVATE cxx_std_20)
target_compile_features(case117_no_unique_address_v2 PRIVATE cxx_std_20)
