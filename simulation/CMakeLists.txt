cmake_minimum_required(VERSION 3.16)
project(quad_sim VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(quadsim_core
    src/csv_io.cpp
    src/pgm.cpp
    src/generate.cpp
    src/evaluate.cpp
)
target_include_directories(quadsim_core PUBLIC include)
target_compile_options(quadsim_core PRIVATE -Wall -Wextra -Wpedantic -Werror)

add_executable(quad-sim src/main.cpp)
target_link_libraries(quad-sim PRIVATE quadsim_core)
target_compile_options(quad-sim PRIVATE -Wall -Wextra -Wpedantic -Werror)

add_executable(quadsim_tests tests/test_quadsim.cpp)
target_link_libraries(quadsim_tests PRIVATE quadsim_core)
target_compile_options(quadsim_tests PRIVATE -Wall -Wextra -Wpedantic -Werror)

enable_testing()
add_test(NAME quadsim_tests COMMAND quadsim_tests)
