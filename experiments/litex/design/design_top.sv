// Mixed-source pilot: a unifpga PDM decoder feeding a generated LiteX FIFO.
// The microphone cannot be backpressured. Samples arriving while the FIFO is
// full are dropped and counted; consumers can backpressure the FIFO output.
module design_top (
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
        .clk_mhz(24), .decimation(8), .output_w(16)
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
