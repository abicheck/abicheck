# Reference build for the abicheck Clang facts plugin (ADR-035 D5, ADR-038 Flow C).
# Optional optimization — not part of the abicheck CI build. The plugin is
# ABI-locked to the loading clang's LLVM major (ADR-038 C.5): build it once
# against your pinned toolchain image and inject it.
# C is enabled alongside CXX because LLVM's FindFFI/FindTerminfo modules (pulled
# in by find_package(LLVM)) run check_c_source_compiles, which requires the C
# language to be enabled in the project.
cmake_minimum_required(VERSION 3.20)
project(abicheck_facts_plugin CXX C)

find_package(LLVM REQUIRED CONFIG)
find_package(Clang REQUIRED CONFIG)

add_library(abicheck-facts MODULE AbicheckFactsPlugin.cpp)
target_include_directories(abicheck-facts SYSTEM PRIVATE ${LLVM_INCLUDE_DIRS} ${CLANG_INCLUDE_DIRS})
target_compile_definitions(abicheck-facts PRIVATE ${LLVM_DEFINITIONS})
target_compile_features(abicheck-facts PRIVATE cxx_std_17)

# LLVM/Clang are conventionally built without RTTI; a plugin that links their
# symbols must match, or it fails to load (undefined typeinfo). Honor the
# loading toolchain's setting when CMake exposes it.
if(NOT LLVM_ENABLE_RTTI)
  target_compile_options(abicheck-facts PRIVATE -fno-rtti)
endif()

# Clang plugins link against the loading clang's symbols; do not bundle LLVM.
set_target_properties(abicheck-facts PROPERTIES PREFIX "lib")
