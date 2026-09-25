module graph_leaf #(
  parameter type T = logic [7:0],
  parameter int W = 8
) (input T data, output T q);
  assign q = data;
endmodule

module graph_legacy(clk, q);
  input clk;
  output [7:0] q;
  assign q = {8{clk}};
endmodule

interface graph_bus(input logic clk);
  logic data;
  modport source(output data, input clk);
endinterface

module graph_consumer(graph_bus.source bus_port);
  assign bus_port.data = bus_port.clk;
endmodule

module graph_top(input logic clk, input logic [7:0] data, output logic [7:0] q);
  graph_bus link(clk);
  graph_consumer consumer(link);
  for (genvar i = 0; i < 2; i++) begin : bank
    graph_leaf #(.T(logic [7:0]), .W(8)) leaf (
      .data(data + 8'd1), .q()
    );
  end
  graph_legacy legacy (.clk(clk), .q(q));
endmodule
