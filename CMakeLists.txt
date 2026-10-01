cmake_minimum_required(VERSION 3.16)
project(CharanGPU_TFX CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra -Wpedantic -Werror)
add_executable(tfx_ref software/reference/tfx_ref.cpp)
target_include_directories(tfx_ref PRIVATE software/reference)
add_executable(tfx_sim simulator/src/tfx_sim.cpp)
target_include_directories(tfx_sim PRIVATE software/reference simulator/include)
enable_testing()
add_test(NAME alu_vectors COMMAND tfx_ref --vectors ${CMAKE_BINARY_DIR}/alu_vectors.txt 1000 1)
add_test(NAME vector_add_both_models
         COMMAND ${CMAKE_COMMAND} -E env python3 ${CMAKE_SOURCE_DIR}/tools/cosim/cosim.py --no-rtl
                 --config ${CMAKE_SOURCE_DIR}/architecture/configuration/tfx_base.json
                 --program ${CMAKE_SOURCE_DIR}/examples/vector_add/vector_add.tfx --arg 3=0)
