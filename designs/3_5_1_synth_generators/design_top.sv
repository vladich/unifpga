// =============================================================================
// 3_5_1_synth_generators
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

