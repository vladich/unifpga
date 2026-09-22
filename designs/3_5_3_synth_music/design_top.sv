// =============================================================================
// 3_5_3_synth_music — auto-adapted by tools/adapt_designs.py from
//   basics-graphics-music/labs/3_music/3_5_3_synth_music/lab_top.sv
// =============================================================================
//
// requires:
//   switches >= 3

module design_top
# (
    parameter int clk_mhz       = 50,
                  w_sw          = 0,
                  w_btn         = 0,
                  w_led         = 0,
                  w_digit       = 0,
                  w_rgb_led     = 0,
                  screen_width  = 0,
                  screen_height = 0,
                  w_red         = 0,
                  w_green       = 0,
                  w_blue        = 0,
                  w_gpio        = 0,
                  w_x = (screen_width  > 0) ? $clog2(screen_width ) : 1,
                  w_y = (screen_height > 0) ? $clog2(screen_height) : 1
)
(
    input                            clk,
    input                            rst,
    input        [w_sw     - 1 : 0]  sw,
    input        [w_btn    - 1 : 0]  btn,
    output logic [w_led    - 1 : 0]  led,
    output logic [          7 : 0]   abcdefgh,
    output logic [w_digit  - 1 : 0]  digit,
    output logic [w_rgb_led- 1 : 0]  rgb_r,
    output logic [w_rgb_led- 1 : 0]  rgb_g,
    output logic [w_rgb_led- 1 : 0]  rgb_b,
    input        [w_x      - 1 : 0]  x,
    input        [w_y      - 1 : 0]  y,
    output logic [w_red    - 1 : 0]  red,
    output logic [w_green  - 1 : 0]  green,
    output logic [w_blue   - 1 : 0]  blue,
    input        [         23 : 0]   mic_sample,
    input                            mic_valid,
    output logic [         15 : 0]   sound,
    input                            uart_rx,
    output logic                     uart_tx,
    inout        [w_gpio   - 1 : 0]  gpio
);


    //------------------------------------------------------------------------

    // assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
    // assign sound      = '0;
       assign uart_tx    = '1;

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

