cmake_minimum_required(VERSION 2.8)
project(tmppy)

file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/py2tmp_test_config.py"
     CONTENT "
CXX='${CMAKE_CXX_COMPILER}'
CXX_COMPILER_NAME='${CMAKE_CXX_COMPILER_ID}'
CXX_COMPILER_VERSION='${CMAKE_CXX_COMPILER_VERSION}'
ADDITIONAL_LINKER_FLAGS='${CMAKE_EXE_LINKER_FLAGS}'
CMAKE_BUILD_TYPE='${CMAKE_BUILD_TYPE}'
MPYL_INCLUDE_DIR='${CMAKE_CURRENT_SOURCE_DIR}/include'
")

file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/pytest.ini"
     CONTENT "
[pytest]
testpaths = \"${CMAKE_CURRENT_SOURCE_DIR}/tests\"
addopts = -r a --tb=short
")

file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/conftest.py"
     CONTENT "
")

add_subdirectory(tests)
