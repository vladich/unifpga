module top #(parameter WIDTH = 4) (
    input logic clk,
    output logic [WIDTH-1:0] count
);
    pulse #(.WIDTH(WIDTH)) u_pulse (.clk(clk), .count(count));
endmodule
