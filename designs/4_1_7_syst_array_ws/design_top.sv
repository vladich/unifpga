// =============================================================================
// 4_1_7_syst_array_ws
// =============================================================================
//

`timescale 1ns / 1ps

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

    // no RGB LEDs used
    assign rgb_r = '0;
    assign rgb_g = '0;
    assign rgb_b = '0;


    localparam W11 = 8'd2;
    localparam W12 = 8'd3;
    localparam W13 = 8'd4;
    localparam W21 = 8'd5;
    localparam W22 = 8'd6;
    localparam W23 = 8'd7;


    localparam W_WIDTH = 8;
    localparam X_WIDTH = 8;

    logic [7:0]  x1_pipe;
    logic [7:0]  x2_pipe;
    logic [7:0]  x3_pipe;
    logic [16:0] psumm11;
    logic [17:0] psumm12;
    logic [18:0] psumm13;
    logic [16:0] psumm21;
    logic [17:0] psumm22;
    logic [18:0] psumm23;

    syst_node #(
        .W_WIDTH  (8),
        .X_WIDTH  (8),
        .SI_WIDTH (1),
        .SO_WIDTH (17)
    ) node_11 (
        .clk      (clk),
        .rst      (rst),
        .weight_i (W11),
        .psumm_i  ('0),
        .x_i      (x1),
        .psumm_o  (psumm11),
        .x_o      (x1_pipe)
    );


    syst_node #(
        .W_WIDTH  (8),
        .X_WIDTH  (8),
        .SI_WIDTH (17),
        .SO_WIDTH (18)
    ) node_12 (
        .clk      (clk),
        .rst      (rst),
        .weight_i (W12),
        .psumm_i  (psumm11),
        .x_i      (x2),
        .psumm_o  (psumm12),
        .x_o      (x2_pipe)
    );

    // Exercise: Add nodes 13 and 23 to systolic array

    // syst_node #(
    //     .W_WIDTH  (8),
    //     .X_WIDTH  (8),
    //     .SI_WIDTH (18),
    //     .SO_WIDTH (19)
    // ) node_13 (
    //     .clk      (clk),
    //     .rst      (rst),
    //     .weight_i (),
    //     .psumm_i  (),
    //     .x_i      (),
    //     .psumm_o  (),
    //     .x_o      ()
    // );


    syst_node #(
        .W_WIDTH  (8),
        .X_WIDTH  (8),
        .SI_WIDTH (1),
        .SO_WIDTH (17)
    ) node_21 (
        .clk      (clk),
        .rst      (rst),
        .weight_i (W21),
        .psumm_i  ('0),
        .x_i      (x1_pipe),
        .psumm_o  (psumm21),
        .x_o      ()
    );


    syst_node #(
        .W_WIDTH  (8),
        .X_WIDTH  (8),
        .SI_WIDTH (17),
        .SO_WIDTH (18)
    ) node_22 (
        .clk      (clk),
        .rst      (rst),
        .weight_i (W22),
        .psumm_i  (psumm21),
        .x_i      (x2_pipe),
        .psumm_o  (psumm22),
        .x_o      ()
    );

    // Exercise: Add nodes 13 and 23 to systolic array

    // syst_node #(
    //     .W_WIDTH  (8),
    //     .X_WIDTH  (8),
    //     .SI_WIDTH (18),
    //     .SO_WIDTH (19)
    // ) node_23 (
    //     .clk      (clk),
    //     .rst      (rst),
    //     .weight_i (),
    //     .psumm_i  (),
    //     .x_i      (),
    //     .psumm_o  (),
    //     .x_o      ()
    // );


    assign y1 = psumm12;
    assign y2 = psumm22;
endmodule

