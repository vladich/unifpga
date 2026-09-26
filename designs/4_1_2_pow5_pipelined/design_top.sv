// =============================================================================
// 4_1_2_pow5_pipelined
// =============================================================================
//
// requires:
//   seven_segment >= 1

module design_top
`include "design_top_interface.svh"

    // ---- slow_clk derivation --------------------------------------------------
    // design_top has no `slow_clk` port, so derive a ~1 Hz tick from
    // the system clock.
    // clk_mhz <= 1 is the testbench setting (tb.sv passes clk as slow_clk):
    // a two-bit divider keeps the simulation short.
    localparam int W_SLOW_CLK_DIV = (clk_mhz > 1) ? $clog2(clk_mhz * 1_000_000) : 2;
    logic [W_SLOW_CLK_DIV - 1 : 0] slow_clk_div;
    logic                          slow_clk;
    always_ff @(posedge clk or posedge rst)
        if (rst) slow_clk_div <= '0;
        else     slow_clk_div <= slow_clk_div + 1'b1;
    assign slow_clk = slow_clk_div[W_SLOW_CLK_DIV - 1];



    //------------------------------------------------------------------------

       assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    // Truncate used SW number to 8
    localparam w_sw_actual = (w_sw > 8) ? 8
                                        : w_sw;

    //------------------------------------------------------------------------



    logic [(2*w_sw_actual)-1:0] pow_mul_stage_1;
    logic [(3*w_sw_actual)-1:0] pow_mul_stage_2;
    logic [(4*w_sw_actual)-1:0] pow_mul_stage_3;
    logic [(5*w_sw_actual)-1:0] pow_mul_stage_4;

    logic [(2*w_sw_actual)-1:0] pow_data_stage_1_ff;
    logic [(3*w_sw_actual)-1:0] pow_data_stage_2_ff;
    logic [(4*w_sw_actual)-1:0] pow_data_stage_3_ff;
    logic [(5*w_sw_actual)-1:0] pow_data_stage_4_ff;

    logic [w_sw_actual-1:0] input_stage_0_ff;
    logic [w_sw_actual-1:0] input_stage_1_ff;
    logic [w_sw_actual-1:0] input_stage_2_ff;
    logic [w_sw_actual-1:0] input_stage_3_ff;

    logic [(5*w_sw_actual)-1:0] pow_output;


    // Input data pipeline
    always_ff @ (posedge slow_clk or posedge rst)
        if (rst) begin
            input_stage_0_ff <= '0;
            input_stage_1_ff <= '0;
            input_stage_2_ff <= '0;
            input_stage_3_ff <= '0;
        end
        else begin
            input_stage_0_ff <= sw;
            input_stage_1_ff <= input_stage_0_ff;
            input_stage_2_ff <= input_stage_1_ff;
            input_stage_3_ff <= input_stage_2_ff;
        end


    // Multiply numbers
    assign pow_mul_stage_1 = input_stage_0_ff * input_stage_0_ff;
    assign pow_mul_stage_2 = input_stage_1_ff * pow_data_stage_1_ff;
    assign pow_mul_stage_3 = input_stage_2_ff * pow_data_stage_2_ff;
    assign pow_mul_stage_4 = input_stage_3_ff * pow_data_stage_3_ff;

    always_ff @ (posedge slow_clk or posedge rst)
        if (rst) begin
            pow_data_stage_1_ff <= '0;
            pow_data_stage_2_ff <= '0;
            pow_data_stage_3_ff <= '0;
            pow_data_stage_4_ff <= '0;
        end
        else begin
            pow_data_stage_1_ff <= pow_mul_stage_1;
            pow_data_stage_2_ff <= pow_mul_stage_2;
            pow_data_stage_3_ff <= pow_mul_stage_3;
            pow_data_stage_4_ff <= pow_mul_stage_4;
        end

    assign pow_output = pow_data_stage_4_ff;


    localparam w_display_number = w_digit * 4;

    seven_segment_display # (w_digit) i_7segment
    (
        .clk      ( clk                            ),
        .rst      ( rst                            ),
        .number   ( (pow_output) ),
        .dots     ( (0)                   ),
        .abcdefgh ( abcdefgh                       ),
        .digit    ( digit                          )
    );
endmodule

