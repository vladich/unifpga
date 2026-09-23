// =============================================================================
// adc_parallel_sampler — an 8-bit parallel ADC (marsohod3gw2's on-board
// ADC_D [7:0] / ADC_CLK) read as the microphone; the equivalent inline logic:
//
//   reg [9:0] adc_cnt_ = 0;  reg [7:0] adc_ = 0;
//   assign ADC_CLK = adc_cnt_ [1];
//   always @ (posedge pixel_clk) begin adc_cnt_ <= adc_cnt_ + 1;
//                                      if (adc_cnt_ == 0) adc_ <= ADC_D; end
//   assign mic = { adc_ ^ 8'h80, 16'h0000 };
//
// The ADC is clocked at clk / 4 and sampled once every 1024 clocks; its
// offset-binary code becomes a signed sample in the top bits. No reset: the
// flops start from their initial values.
// =============================================================================

module adc_parallel_sampler
(
    input               clk,
    input        [7:0]  adc_d,
    output              adc_clk,
    output       [23:0] value
);

    reg [9:0] cnt    = '0;
    reg [7:0] sample = '0;

    assign adc_clk = cnt [1];

    always @ (posedge clk)
    begin
        cnt <= cnt + 1'd1;

        if (cnt == '0)
            sample <= adc_d;
    end

    assign value = { sample ^ 8'h80, 16'h0000 };

endmodule
