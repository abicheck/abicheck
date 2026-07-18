cmake_minimum_required(VERSION 3.16)
project(abicheck-clang-layout-tool C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(Clang REQUIRED CONFIG)
message(STATUS "Found Clang ${Clang_VERSION} (LLVM ${LLVM_VERSION})")

add_executable(abicheck-clang-layout-tool src/main.cpp)
target_include_directories(abicheck-clang-layout-tool PRIVATE ${CLANG_INCLUDE_DIRS})
target_compile_definitions(abicheck-clang-layout-tool PRIVATE ${CLANG_DEFINITIONS})
target_link_libraries(abicheck-clang-layout-tool PRIVATE
  clangTooling
  clangFrontend
  clangAST
  clangBasic
)

install(TARGETS abicheck-clang-layout-tool RUNTIME DESTINATION bin)
