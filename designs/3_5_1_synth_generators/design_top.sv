// =============================================================================
// 3_5_1_synth_generators
// =============================================================================
//

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

    logic [7:0] sample_data_square;
    logic [7:0] sample_data_saw;
    logic [7:0] sample_data_saw_inv;
    logic [7:0] sample_data_triangle;
    logic [7:0] sample_data_sine;
    logic [7:0] sample_data_noise;

    localparam freq = 440; // In Hz
    localparam bit [63:0] generator_freq =(((2**27) * freq)/(clk_mhz * 1000000))-1;

    audio_square inst_square
    (
      .clk_i         (clk),
      .rst_i         (rst),
      .freq_i        (generator_freq),
      .sample_data_o (sample_data_square)
    );

    audio_saw inst_saw
    (
      .clk_i         (clk),
      .rst_i         (rst),
      .freq_i        (generator_freq),
      .sample_data_o (sample_data_saw)
    );

    audio_saw_inv inst_saw_inv
    (
      .clk_i         (clk),
      .rst_i         (rst),
      .freq_i        (generator_freq),
      .sample_data_o (sample_data_saw_inv)
    );

    audio_triangle inst_triangle
    (
      .clk_i         (clk),
      .rst_i         (rst),
      .freq_i        (generator_freq),
      .sample_data_o (sample_data_triangle)
    );

    audio_sine inst_sine
    (
      .clk_i         (clk),
      .rst_i         (rst),
      .freq_i        (generator_freq),
      .sample_data_o (sample_data_sine)
    );

    audio_noise inst_noise
    (
      .clk_i         (clk),
      .rst_i         (rst),
      .freq_i        (generator_freq),
      .sample_data_o (sample_data_noise)
    );

    logic [7:0] sound_mux;

    always_comb begin
        case(sw)
          'd0: sound_mux = sample_data_square;
          'd1: sound_mux = sample_data_saw;
          'd2: sound_mux = sample_data_saw_inv;
          'd3: sound_mux = sample_data_triangle;
          'd4: sound_mux = sample_data_sine;
          'd5: sound_mux = sample_data_noise;
          default: sound_mux = '0;
        endcase

    end

    assign sound = {1'd0, sound_mux, 7'd0};
endmodule

