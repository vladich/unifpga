// =============================================================================
// 3_5_2_synth_channel
// =============================================================================
//
// requires:
//   switches >= 3

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

    audio_channel audio_channel_inst
    (
      .clk_i         (clk),
      .rst_i         (rst),
      .en_i          (1'b1),
      .gen_sel_i     (sw[2:0]),
      .freq_i        (generator_freq),
      .volume_i      (8'd255),
      .sample_data_o (sample_data)
    );

    assign sound = {1'd0, sample_data, 7'd0};
endmodule

