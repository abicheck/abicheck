abicheck_add_case(case114_char8t_migration
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    APP_SOURCES app.cpp
    PLATFORMS linux
)

# char8_t is a C++20 feature; the repo-wide default is C++17.
# Guard on TARGET: this case is linux-only, so on macOS/Windows
# abicheck_add_case() returns early and the targets do not exist —
# referencing them unconditionally would fail the shared configure.
if(TARGET case114_char8t_migration_v1)
    target_compile_features(case114_char8t_migration_v1 PRIVATE cxx_std_20)
    target_compile_features(case114_char8t_migration_v2 PRIVATE cxx_std_20)
endif()
