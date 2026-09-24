`include "config.svh"

module top(input logic clk, output logic [3:0] q);
  import numbers_pkg::*;
  localparam int COUNT_WIDTH = WIDTH;
  logic [COUNT_WIDTH-1:0] counter;
  stream_if #(.WIDTH(COUNT_WIDTH)) unused_stream(clk);
`ifdef FEATURE
  always_ff @(posedge clk) counter <= counter + `INCREMENT;
`else
  always_ff @(posedge clk) counter <= '0;
`endif
  assign q = counter;
endmodule
