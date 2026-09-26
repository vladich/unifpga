// =============================================================================
// 3_5_3_synth_music
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

    logic [3:0] note; // C, Cd, D, Dd, E, F, Fd, G, Gd, A, Ad, B
    logic [1:0] octave; // THIRD, SECOND, FIRST, SMALL
    logic       enable;

    melody_memory
    #(
      .CLK_MHZ (clk_mhz),
      .BPM     (80)
    )
    inst_memory_imperial_march
    (
      .clk_i (clk),
      .rst_i (rst),

      .note_o (note), // C, Cd, D, Dd, E, F, Fd, G, Gd, A, Ad, B
      .octave_o (octave), // THIRD, SECOND, FIRST, SMALL
      .enable_o (enable)
    );

    logic [15:0] freq_music;

    note_freq_mem #(
      .CLK_MHZ (clk_mhz)
    )
    inst_note_rom
    (
      .note_sel_i   (note),
      .octave_sel_i (octave),

      .freq_o (freq_music)
    );

    logic [7:0] sample_data;

    audio_channel audio_channel_inst
    (
      .clk_i         (clk),
      .rst_i         (rst),
      .en_i          (enable),
      .gen_sel_i     (sw[2:0]),
      .freq_i        (freq_music),
      .volume_i      (8'd255),
      .sample_data_o (sample_data)
    );

    assign sound = {1'd0, sample_data, 7'd0};
endmodule

