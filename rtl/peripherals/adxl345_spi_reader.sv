// =============================================================================
// adxl345_spi_reader — reads the three axes of an Analog Devices ADXL345
// accelerometer over 4-wire SPI (the DE10-Lite's; mode 3: SCLK idles high,
// data changes on its falling edge and is sampled on the rising one) and gives
// them in milli-g.
//
// Through spi_reg_master: once, POWER_CTL (0x2D) = 0x08, measurement mode;
// then every 1000 / POLL_HZ ms a burst read of the six data registers from
// DATAX0: the command byte 0xF2 = read (0x80) | multiple bytes (0x40) | 0x32,
// then six bytes in. adxl345_axes turns them into milli-g. SCLK at most 5 MHz.
// =============================================================================

module adxl345_spi_reader
# (
    parameter CLK_MHZ  = 50,
    parameter SCLK_KHZ = 1000,
    parameter POLL_HZ  = 100,
    parameter START_MS = 2
)
(
    input                      clk,
    input                      rst,
    output                     sclk,
    output                     mosi,
    input                      miso,
    output                     cs_n,
    output                     valid,     // one clock: new readings
    output signed [15:0]       x,         // milli-g
    output signed [15:0]       y,
    output signed [15:0]       z
);

    wire        got;
    wire [47:0] data;

    spi_reg_master
    # (
        .CLK_MHZ (CLK_MHZ), .SCLK_KHZ (SCLK_KHZ), .CPOL (1), .CPHA (1),
        .START_MS (START_MS), .POLL_HZ (POLL_HZ),
        .N_INIT (1), .INIT_LEN (2), .INIT ({ 8'h2D, 8'h08 }),   // POWER_CTL: measure
        .CMD_LEN (1), .CMD (8'hF2),                              // read, multiple bytes, DATAX0
        .N_BYTES (6)
    )
    i_master
    (
        .clk (clk), .rst (rst), .sclk (sclk), .mosi (mosi), .miso (miso), .cs_n (cs_n),
        .valid (got), .data (data)
    );

    adxl345_axes i_axes (.clk (clk), .rst (rst), .got (got), .data (data),
                         .valid (valid), .x (x), .y (y), .z (z));

endmodule
