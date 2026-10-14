cmake_minimum_required(VERSION 3.16)
project(quad_vision LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(QUAD_VISION_SANITIZERS "Enable AddressSanitizer and UndefinedBehaviorSanitizer" ON)

add_library(quad_vision
  src/pgm.cpp
  src/manifest.cpp
  src/flow_csv.cpp
  src/kernel.cpp
  src/reference.cpp
  src/process.cpp
)
target_include_directories(quad_vision PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(quad_vision PUBLIC cxx_std_17)

function(quad_vision_warnings target)
  target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic -Wshadow -Werror)
endfunction()

quad_vision_warnings(quad_vision)

add_executable(quad-vision src/main.cpp)
target_link_libraries(quad-vision PRIVATE quad_vision)
quad_vision_warnings(quad-vision)

add_executable(quad-vision-bench bench/bench.cpp)
target_link_libraries(quad-vision-bench PRIVATE quad_vision)
target_include_directories(quad-vision-bench PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)
quad_vision_warnings(quad-vision-bench)

add_executable(quad-vision-tests tests/test_main.cpp)
target_link_libraries(quad-vision-tests PRIVATE quad_vision)
target_include_directories(quad-vision-tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)
quad_vision_warnings(quad-vision-tests)
target_compile_definitions(quad-vision-tests PRIVATE
  "QUAD_VISION_EXECUTABLE=\"$<TARGET_FILE:quad-vision>\""
  "QUAD_VISION_BENCH=\"$<TARGET_FILE:quad-vision-bench>\""
  "QUAD_VISION_SOURCE_DIR=\"${CMAKE_CURRENT_SOURCE_DIR}\""
)

if(QUAD_VISION_SANITIZERS)
  foreach(target quad_vision quad-vision quad-vision-tests quad-vision-bench)
    target_compile_options(${target} PRIVATE
      -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(${target} PRIVATE -fsanitize=address,undefined)
  endforeach()
endif()

enable_testing()
add_test(NAME quad-vision-tests COMMAND quad-vision-tests)
