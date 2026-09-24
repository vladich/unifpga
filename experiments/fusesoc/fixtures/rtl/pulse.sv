module pulse #(parameter WIDTH = 4) (
    input logic clk,
    output logic [WIDTH-1:0] count
);
    always_ff @(posedge clk) count <= count + 1'b1;
endmodule
