module parameter_top #(
  parameter int WIDTH = 1
) (
  input logic [WIDTH-1:0] data,
  output logic [WIDTH-1:0] q
);
  localparam int INTERNAL = WIDTH + 1;
  assign q = data;
endmodule
