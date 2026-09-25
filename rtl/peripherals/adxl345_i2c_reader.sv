// =============================================================================
// adxl345_i2c_reader — reads the three axes of an Analog Devices ADXL345
// accelerometer over I2C (the DE0-Nano's, its CS pin held high for I2C mode)
// and gives them in milli-g.
//
// Through i2c_reg_master: once, POWER_CTL (0x2D) = 0x08, measurement mode;
// then every 1000 / POLL_HZ ms a burst of the six data registers from DATAX0
// (0x32): X, Y, Z, each least significant byte first, 10 bits sign-extended to
// 16; adxl345_axes turns them into milli-g. The device answers at 0x1D or 0x53
// by its ALT ADDRESS strap: both are tried.
// =============================================================================

module adxl345_i2c_reader
# (
    parameter CLK_MHZ = 50,
    parameter SCL_KHZ = 100,
    parameter POLL_HZ = 100
)
(
    input                      clk,
    input                      rst,
    output                     scl,
    inout                      sda,
    output                     valid,     // one clock: new readings
    output signed [15:0]       x,         // milli-g
    output signed [15:0]       y,
    output signed [15:0]       z
);

    localparam int POLL_MS = (1000 / POLL_HZ > 0) ? 1000 / POLL_HZ : 1;

    wire        got;
    wire [47:0] data;                     // X0 X1 Y0 Y1 Z0 Z1, the first byte in the top

    i2c_reg_master
    # (
        .CLK_MHZ (CLK_MHZ), .SCL_KHZ (SCL_KHZ), .POLL_MS (POLL_MS),
        .N_ADDR  (2),
        .ADDRS   ({ 7'h1D, 7'h53 }),
        .N_INIT  (1),
        .INIT    ({ 8'h2D, 8'h08 }),      // POWER_CTL: measure
        .REG     (8'h32),                 // DATAX0
        .N_BYTES (6)
    )
    i_master
    (
        .clk (clk), .rst (rst), .scl (scl), .sda (sda),
        .valid (got), .data (data), .found ()
    );

    adxl345_axes i_axes (.clk (clk), .rst (rst), .got (got), .data (data),
                         .valid (valid), .x (x), .y (y), .z (z));

endmodule
