// Asynchronous reset here is needed for some FPGA boards we use


module shift_reg
# (
    parameter depth = 2
)
(
    input                      clk,
    input                      rst,
    input                      en,
    input                      seq_in,
    output                     seq_out,
    output logic [depth - 1:0] par_out
);

    // depth 1 (a one-LED board): the register is the input bit alone
    wire [depth - 1:0] next;

    generate
        if (depth == 1)
            assign next = seq_in;
        else
            assign next = { seq_in, par_out [depth - 1:1] };
    endgenerate

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            par_out <= '0;
        else if (en)
            par_out <= next;

    assign seq_out = par_out [0];

endmodule
