// Virtual-device-facing wrapper for the mixed-source PDM/FIFO pilot. A raw PDM
// microphone uses gpio[0:2]; btn[0] controls downstream backpressure. This is
// a structural adapter, not evidence of safe GPIO voltage or microphone timing.
// requires:
//   gpio >= 3
//   buttons >= 1
//   leds >= 1
module design_top #(
    parameter int clk_mhz = 24,
                  w_sw = 0, w_btn = 1, w_led = 1, w_digit = 0,
                  w_rgb_led = 0, screen_width = 0, screen_height = 0,
                  w_red = 0, w_green = 0, w_blue = 0, w_gpio = 3,
                  w_x = screen_width > 0 ? $clog2(screen_width) : 1,
                  w_y = screen_height > 0 ? $clog2(screen_height) : 1
) (
    input clk, rst,
    input [w_sw-1:0] sw,
    input [w_btn-1:0] btn,
    output [w_led-1:0] led,
    output [7:0] abcdefgh,
    output [w_digit-1:0] digit,
    output [w_rgb_led-1:0] rgb_r, rgb_g, rgb_b,
    input [w_x-1:0] x,
    input [w_y-1:0] y,
    output [w_red-1:0] red,
    output [w_green-1:0] green,
    output [w_blue-1:0] blue,
    input [23:0] mic_sample,
    input mic_valid,
    output [15:0] sound,
    input uart_rx,
    output uart_tx,
    inout [w_gpio-1:0] gpio
);
    wire pdm_clk, pdm_lrsel, sample_valid;
    wire [15:0] sample_data, dropped_samples;
    wire [2:0] level;

    pdm_fifo_capture #(.clk_mhz(clk_mhz)) capture (
        .clk(clk), .rst(rst), .pdm_clk(pdm_clk), .pdm_data(gpio[0]),
        .pdm_lrsel(pdm_lrsel), .sample_valid(sample_valid),
        .sample_ready(btn[0]), .sample_data(sample_data),
        .level(level), .dropped_samples(dropped_samples)
    );

    assign gpio[0] = 1'bz;
    assign gpio[1] = pdm_clk;
    assign gpio[2] = pdm_lrsel;
    for (genvar i = 3; i < w_gpio; i = i + 1) begin : unused_gpio
        assign gpio[i] = 1'bz;
    end
    assign led = (sample_valid ? sample_data[15:8] : 8'h00) ^ dropped_samples[7:0];
    assign abcdefgh = '0;
    assign digit = '0;
    assign rgb_r = '0;
    assign rgb_g = '0;
    assign rgb_b = '0;
    assign red = '0;
    assign green = '0;
    assign blue = '0;
    assign sound = '0;
    assign uart_tx = 1'b1;
endmodule

// End-to-end stream/peripheral pilot. This recipe requires the generated
// LiteX FIFO at width 16/depth 4 and UART PHY at 24 MHz/115200 baud. The
// native packetizer holds each byte until the PHY acknowledges transmission.
// Input PDM cannot be stalled; the capture FIFO counts lost samples.
module pdm_uart_stream (
    input  wire        clk,
    input  wire        rst,
    input  wire        pdm_data,
    output wire        pdm_clk,
    output wire        pdm_lrsel,
    output wire        uart_tx,
    output wire [2:0]  level,
    output wire [15:0] dropped_samples
);
    wire sample_valid;
    wire sample_ready;
    wire [15:0] sample_data;
    wire byte_valid;
    wire byte_ready;
    wire [7:0] byte_data;

    pdm_fifo_capture #(.clk_mhz(24), .decimation(1024)) capture (
        .clk(clk), .rst(rst), .pdm_clk(pdm_clk), .pdm_data(pdm_data),
        .pdm_lrsel(pdm_lrsel), .sample_valid(sample_valid),
        .sample_ready(sample_ready), .sample_data(sample_data),
        .level(level), .dropped_samples(dropped_samples)
    );

    sample_to_uart_bytes packetizer (
        .clk(clk), .rst(rst),
        .sample_valid(sample_valid), .sample_ready(sample_ready),
        .sample_data(sample_data),
        .byte_valid(byte_valid), .byte_ready(byte_ready),
        .byte_data(byte_data)
    );

    litex_rs232_phy phy (
        .sys_clk(clk), .sys_rst(rst),
        .serial_rx(1'b1), .serial_tx(uart_tx),
        .sink_valid(byte_valid), .sink_ready(byte_ready),
        .sink_data(byte_data),
        .source_valid(), .source_ready(1'b1), .source_data(),
        .rx_framing_error(), .rx_overflow()
    );
endmodule

// Two-byte, big-endian sample encoding. The held word isolates the UART's
// long acknowledgement latency from the FIFO's ready/valid transfer.
module sample_to_uart_bytes (
    input  wire        clk,
    input  wire        rst,
    input  wire        sample_valid,
    output wire        sample_ready,
    input  wire [15:0] sample_data,
    output wire        byte_valid,
    input  wire        byte_ready,
    output wire [7:0]  byte_data
);
    localparam [1:0] WAIT_SAMPLE = 2'd0, SEND_HIGH = 2'd1, SEND_LOW = 2'd2;
    reg [1:0] state;
    reg [15:0] held_sample;

    assign sample_ready = state == WAIT_SAMPLE;
    assign byte_valid = state == SEND_HIGH || state == SEND_LOW;
    assign byte_data = state == SEND_HIGH ? held_sample[15:8] : held_sample[7:0];

    always @(posedge clk) begin
        if (rst) begin
            state <= WAIT_SAMPLE;
            held_sample <= 16'd0;
        end else begin
            case (state)
                WAIT_SAMPLE: if (sample_valid) begin
                    held_sample <= sample_data;
                    state <= SEND_HIGH;
                end
                SEND_HIGH: if (byte_ready) state <= SEND_LOW;
                SEND_LOW: if (byte_ready) state <= WAIT_SAMPLE;
                default: state <= WAIT_SAMPLE;
            endcase
        end
    end
endmodule

// Mixed-source subsystem: a unifpga PDM decoder feeding a generated LiteX
// FIFO. The microphone cannot be backpressured. Samples arriving while the
// FIFO is full are dropped and counted; consumers can stall its output.
module pdm_fifo_capture #(
    parameter int clk_mhz = 24,
                  decimation = 8
) (
    input  wire        clk,
    input  wire        rst,
    output wire        pdm_clk,
    input  wire        pdm_data,
    output wire        pdm_lrsel,
    output wire        sample_valid,
    input  wire        sample_ready,
    output wire [15:0] sample_data,
    output wire [2:0]  level,
    output reg  [15:0] dropped_samples
);
    wire [15:0] decoded_sample;
    wire decoded_valid;
    wire fifo_ready;
    wire first;
    wire last;

    pdm_mic_decoder #(
        .clk_mhz(clk_mhz), .decimation(decimation), .output_w(16)
    ) decoder (
        .clk(clk), .rst(rst), .pdm_clk(pdm_clk), .pdm_data(pdm_data),
        .pdm_lrsel(pdm_lrsel), .sample_o(decoded_sample), .valid_o(decoded_valid)
    );

    litex_sync_fifo fifo (
        .sys_clk(clk), .sys_rst(rst),
        .sink_valid(decoded_valid), .sink_ready(fifo_ready),
        .sink_first(1'b1), .sink_last(1'b1), .sink_data(decoded_sample),
        .source_valid(sample_valid), .source_ready(sample_ready),
        .source_first(first), .source_last(last), .source_data(sample_data),
        .level(level)
    );

    always @(posedge clk) begin
        if (rst)
            dropped_samples <= 16'd0;
        else if (decoded_valid && !fifo_ready && dropped_samples != 16'hffff)
            dropped_samples <= dropped_samples + 16'd1;
    end
endmodule
