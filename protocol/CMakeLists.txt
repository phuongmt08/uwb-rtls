cmake_minimum_required(VERSION 3.13)
project(UwbProtocol C)

set(CMAKE_C_STANDARD 99)

# Paths
set(NANOPB_DIR "${CMAKE_CURRENT_SOURCE_DIR}/nanopb")
set(PROTOS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/protos")

# 1. We manually generated the .pb.c files in build.sh
# So just find them in the protos directory
file(GLOB GENERATED_SRCS "${PROTOS_DIR}/*.pb.c")
message(STATUS "Compiling Sources: ${GENERATED_SRCS}")

# 2. Create Library
# We include pb_encode/decode/common directly from nanopb folder
add_library(uwb_protocol_lib STATIC
    ${GENERATED_SRCS}
    "${NANOPB_DIR}/pb_encode.c"
    "${NANOPB_DIR}/pb_decode.c"
    "${NANOPB_DIR}/pb_common.c"
)

# 3. Include Directories
target_include_directories(uwb_protocol_lib PUBLIC
    "${CMAKE_CURRENT_BINARY_DIR}"
    "${NANOPB_DIR}"
    "${PROTOS_DIR}"
)