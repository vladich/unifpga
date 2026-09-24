interface stream_if #(parameter int WIDTH = 4) (input logic clk);
  logic [WIDTH-1:0] payload;
  modport sink(input payload, input clk);
endinterface
