# CharanGPU TFX-X convenience targets (all real work is in scripts/ and tools/).
CXX ?= g++
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror
.PHONY: all sim ref test test-full rtl lint fuzz mutation bench images synth clean
all: ref sim
build:
	mkdir -p build
ref: | build
	$(CXX) $(CXXFLAGS) -Isoftware/reference software/reference/tfx_ref.cpp -o build/tfx_ref
sim: | build
	$(CXX) $(CXXFLAGS) -Isoftware/reference -Isimulator/include simulator/src/tfx_sim.cpp -o build/tfx_sim
test:
	scripts/run_all.sh
test-full:
	FULL=1 scripts/run_all.sh
rtl: ref
	python3 tests/run_rtl.py
lint:
	verilator --lint-only -Wall -Wno-UNUSEDSIGNAL --top-module charangpu_tfx_top rtl/core/tfx_alu.sv rtl/core/tfx_mul_pipe.sv rtl/core/tfx_decoder.sv \
	  rtl/scheduler/tfx_warp_scheduler.sv rtl/memory/tfx_scratchpad.sv rtl/memory/tfx_lsu.sv rtl/compute/tfx_simt_cluster.sv \
	  rtl/command_processor/tfx_command_processor.sv rtl/interconnect/tfx_fifo.sv rtl/interconnect/tfx_token_channel.sv rtl/top/charangpu_tfx_top.sv
fuzz:
	python3 tools/cosim/fuzz.py --config architecture/configuration/tfx_base.json --count 50 --seed 1
mutation:
	python3 tools/cosim/mutation_test.py
bench:
	python3 benchmarks/run_benchmarks.py
images:
	python3 tools/render_images.py --rtl
synth:
	synthesis/yosys/run_synth.sh
clean:
	rm -rf build
