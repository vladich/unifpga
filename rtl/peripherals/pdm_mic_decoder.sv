// =============================================================================
// pdm_mic_decoder
//
// Decodes a 1-bit PDM stream from an on-board PDM microphone (Knowles SPM2407
// on Nexys 4 DDR / Nexys A7 / Basys 3) into 24-bit signed samples.
//
// The implementation is a minimal CIC-style decimator: a running sum over a
// fixed window producing a sample per `valid_o` strobe. The decoder drives
// `pdm_clk` at the mic's expected rate (clk_mhz / decimation_ratio) and reads
// `pdm_data` on the rising edge.
//
// TODO: tune `DECIMATION` and add a CIC + low-pass FIR for production use.
// The current single-stage running sum is intelligible but noisy; usable for
// designs that demonstrate spectrum/recognition concepts without quality bars.
// =============================================================================

module pdm_mic_decoder
# (
    parameter int clk_mhz       = 50,
    parameter int decimation    = 64,    // PDM samples per output sample
    parameter int output_w      = 24
)
(
    input                        clk,
    input                        rst,

    output                       pdm_clk,    // to mic
    input                        pdm_data,   // from mic
    output                       pdm_lrsel,  // L/R select; tied to ground = left

    output logic [output_w-1:0]  sample_o,
    output logic                 valid_o
);

    assign pdm_lrsel = 1'b0;

    // Generate ~3 MHz pdm_clk by dividing system clock.
    localparam int CLK_DIV = (clk_mhz * 1000_000) / 3_000_000;
    logic [$clog2(CLK_DIV)-1:0] div_cnt;
    logic                        pdm_clk_int;

    always_ff @(posedge clk) begin
        if (rst) begin
            div_cnt    <= '0;
            pdm_clk_int <= 1'b0;
        end else if (div_cnt == CLK_DIV/2 - 1) begin
            div_cnt    <= '0;
            pdm_clk_int <= ~pdm_clk_int;
        end else begin
            div_cnt <= div_cnt + 1'b1;
        end
    end

    assign pdm_clk = pdm_clk_int;

    // Synchronizer + edge detect on pdm_clk_int.
    logic pdm_clk_d;
    always_ff @(posedge clk) pdm_clk_d <= pdm_clk_int;
    wire   pdm_rising = pdm_clk_int & ~pdm_clk_d;

    // Decimator: accumulate decimation PDM samples, then emit signed sample.
    logic [$clog2(decimation+1)-1:0] dec_cnt;
    logic [$clog2(decimation+1)-1:0] sum;
    logic [output_w-1:0]              acc;

    always_ff @(posedge clk) begin
        if (rst) begin
            dec_cnt   <= '0;
            sum       <= '0;
            sample_o  <= '0;
            valid_o   <= 1'b0;
        end else begin
            valid_o <= 1'b0;
            if (pdm_rising) begin
                if (dec_cnt == decimation - 1) begin
                    // Convert sum (0..decimation) to signed centered around zero.
                    sample_o <= ($signed({1'b0, sum}) + $signed({1'b0, pdm_data})
                                 - decimation/2)
                                <<< (output_w - $clog2(decimation+1) - 1);
                    valid_o  <= 1'b1;
                    dec_cnt  <= '0;
                    sum      <= '0;
                end else begin
                    dec_cnt <= dec_cnt + 1'b1;
                    sum     <= sum + pdm_data;
                end
            end
        end
    end

endmodule
