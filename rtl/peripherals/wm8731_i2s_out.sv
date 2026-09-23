// =============================================================================
// wm8731_i2s_out — the on-board Wolfson WM8731 audio codec of the Terasic
// DE1 / DE2 / DE2-115 / DE1-SoC used as an I2S DAC: i2s_audio_out feeds the
// DAC lines and
// Terasic's I2C_AUDIO_Config programs the codec registers once after reset.
//
//     i2s_audio_out # (.clk_mhz (clk_mhz)) i_audio_out (
//         .mclk (AUD_XCK), .bclk (AUD_BCLK), .lrclk (AUD_DACLRCK), .sdata (AUD_DACDAT));
//     I2C_AUDIO_Config i_i2c_codec_conf (.iCLK (clk), .iRST_N (~ rst),
//         .I2C_SCLK (I2C_SCLK), .I2C_SDAT (I2C_SDAT), .READY ());
// =============================================================================

module wm8731_i2s_out
# (
    parameter clk_mhz = 50,
              in_res  = 16
)
(
    input                 clk,
    input                 reset,
    input  [in_res - 1:0] data_in,
    output                xck,        // codec master clock
    output                bclk,
    output                dac_lrck,
    output                dac_dat,
    output                i2c_sclk,
    inout                 i2c_sdat
);

    i2s_audio_out
    # (
        .clk_mhz ( clk_mhz ),
        .in_res  ( in_res  )
    )
    i_i2s
    (
        .clk     ( clk      ),
        .reset   ( reset    ),
        .data_in ( data_in  ),
        .mclk    ( xck      ),
        .bclk    ( bclk     ),
        .lrclk   ( dac_lrck ),
        .sdata   ( dac_dat  )
    );

    I2C_AUDIO_Config i_conf
    (
        .iCLK     ( clk      ),
        .iRST_N   ( ~ reset  ),
        .I2C_SCLK ( i2c_sclk ),
        .I2C_SDAT ( i2c_sdat ),
        .READY    (          )
    );

endmodule
