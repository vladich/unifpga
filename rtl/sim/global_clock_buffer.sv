// global_clock_buffer in simulation: a wire. For a build the generated top
// defines it for the board (tools/codegen.py _CLOCK_BUFFERS: Intel's GLOBAL,
// Xilinx's BUFG, or a wire), so designs never test the vendor.

module global_clock_buffer (input in, output out);
    assign out = in;
endmodule
