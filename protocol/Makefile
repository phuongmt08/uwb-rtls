# Protocol Generator Makefile
# Usage:
#   make        - Generate .pb.h/.pb.c from .proto files
#   make clean  - Remove generated files

PROTO_DIR := protos
NANOPB_GEN := nanopb/generator/nanopb_generator.py
PROTO_PY := ../software/common/protocol_pb2.py

# Auto-detect Python command - can be overridden via: make PYTHON="py -3"
ifndef PYTHON
  PYTHON_TEST := $(shell py -3 -V 2>&1)
  ifneq ($(findstring Python,$(PYTHON_TEST)),)
    PYTHON := py -3
  else
    PYTHON_TEST := $(shell python3 -V 2>&1)
    ifneq ($(findstring Python,$(PYTHON_TEST)),)
      PYTHON := python3
    else
      PYTHON_TEST := $(shell python -V 2>&1)
      ifneq ($(findstring Python,$(PYTHON_TEST)),)
        PYTHON := python
      else
        $(error Python not found. Install Python or set PYTHON variable: make PYTHON="py -3")
      endif
    endif
  endif
endif

# Find all .proto files
PROTO_FILES := $(wildcard $(PROTO_DIR)/*.proto)
GENERATED_C := $(PROTO_FILES:.proto=.pb.c)
GENERATED_H := $(PROTO_FILES:.proto=.pb.h)
GENERATED_PY := $(PROTO_PY)

.PHONY: all clean install-deps

all: install-deps $(GENERATED_C) $(GENERATED_PY)

install-deps:
	@echo ">>> Checking Python dependencies..."
	@$(PYTHON) -m pip install --quiet protobuf grpcio-tools || true

# Rule to generate .pb.c and .pb.h from .proto
%.pb.c %.pb.h: %.proto
	@echo ">>> Generating: $<"
	#NOTE: python nanopb_generator.py -I protos -D protos protos/example.proto
	@$(PYTHON) $(NANOPB_GEN) -I $(PROTO_DIR) -D $(PROTO_DIR) $<

$(PROTO_PY): $(PROTO_FILES)
	#NOTE: python -m grpc_tools.protoc -I protos --python_out=../software/common protos/example.proto
	@echo ">>> Generating Python protobuf for software..."
	@$(PYTHON) -m grpc_tools.protoc -I $(PROTO_DIR) --python_out=../software/common $(PROTO_FILES)

clean:
	@echo ">>> Cleaning generated files..."
	@rm -f $(PROTO_DIR)/*.pb.c $(PROTO_DIR)/*.pb.h
	@rm -f $(PROTO_PY)
	@echo ">>> Clean complete"

# Display help
help:
	@echo "Protocol Makefile"
	@echo ""
	@echo "Targets:"
	@echo "  make         - Generate protobuf C files and common/protocol_pb2.py"
	@echo "  make clean   - Remove generated protobuf outputs"
	@echo "  make help    - Show this help"
