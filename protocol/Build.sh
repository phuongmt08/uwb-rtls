#!/bin/bash
set -e

# Hardcoded Paths
PYTHON_EXE="C:/Users/Admin/AppData/Local/Programs/Python/Python312/python.exe"
PROJECT_DIR=$(dirname "$0")
NANOPB_GEN="$PROJECT_DIR/nanopb/generator/nanopb_generator.py"
PROTO_DIR="$PROJECT_DIR/protos"

echo ">>> PHASE 0: INSTALLING DEPENDENCIES (IF MISSING)"
"$PYTHON_EXE" -m pip install protobuf grpcio-tools > /dev/null

echo ">>> PHASE 1: GENERATING C CODE FROM PROTO"
# Loop through all .proto files and generate C code immediately
for proto_file in "$PROTO_DIR"/*.proto; do
    echo "Processing: $proto_file"
    "$PYTHON_EXE" "$NANOPB_GEN" -I "$PROTO_DIR" -D "$PROTO_DIR" "$proto_file"
done

echo ">>> PHASE 2: COMPILING WITH CMAKE"
# Clean build
cd "$PROJECT_DIR"
if [ -d "build" ]; then
    rm -rf build
fi
mkdir build
cd build

# Configure (CMake now purely handles C code)
cmake -G "MinGW Makefiles" ..

# Build
cmake --build .

echo ">>> BUILD SUCCESS!"