// =============================================================================
// 3_5_5_synth_modulation_fm
// =============================================================================
//

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

    // assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
    // assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    localparam freq = 440; // In Hz
    localparam bit [63:0] generator_freq =(((2**27) * freq)/(clk_mhz * 1000000))-1;

    logic [7:0] sample_data;

    mod_frequency mod_frequency_inst
    (
    .clk_i         (clk),
    .rst_i         (rst),
    .freq_i        (generator_freq),
    .sample_data_o (sample_data)
    );


    assign sound = {1'd0, sample_data, 7'd0};
endmodule

