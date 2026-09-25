// =============================================================================
// adxl362_reader — reads the three axes of an Analog Devices ADXL362
// accelerometer over SPI (mode 0: SCLK idles low, both sides sample on its
// rising edge) and gives them in milli-g.
//
// Through spi_reg_master: after the part's 5 ms start-up, POWER_CTL (0x2D) =
// 0x02, measurement mode (the write command 0x0A). Then every 1000 / POLL_HZ
// ms a burst of the six data registers from XDATA_L (0x0E; the read command
// 0x0B): X, Y, Z, each least significant byte first, 12 bits sign-extended to
// 16. At the power-on range (+-2 g) one count is 1 mg, so the readings are
// milli-g as they are. Datasheet: "SPI commands".
// =============================================================================

module adxl362_reader
# (
    parameter CLK_MHZ   = 50,
    parameter SCLK_KHZ  = 1000,
    parameter POLL_HZ   = 100,
    parameter START_MS  = 6
)
(
    input                      clk,
    input                      rst,
    output                     sclk,
    output                     mosi,
    input                      miso,
    output                     cs_n,
    output logic               valid,     // one clock: new readings
    output logic signed [15:0] x,         // milli-g
    output logic signed [15:0] y,
    output logic signed [15:0] z
);

    wire        got;
    wire [47:0] data;                     // xl xh yl yh zl zh, the first byte in the top

    spi_reg_master
    # (
        .CLK_MHZ (CLK_MHZ), .SCLK_KHZ (SCLK_KHZ), .CPOL (0), .CPHA (0),
        .START_MS (START_MS), .POLL_HZ (POLL_HZ),
        .N_INIT (1), .INIT_LEN (3), .INIT ({ 8'h0A, 8'h2D, 8'h02 }),   // write POWER_CTL: measure
        .CMD_LEN (2), .CMD ({ 8'h0B, 8'h0E }),                          // read from XDATA_L
        .N_BYTES (6)
    )
    i_master
    (
        .clk (clk), .rst (rst), .sclk (sclk), .mosi (mosi), .miso (miso), .cs_n (cs_n),
        .valid (got), .data (data)
    );

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            valid <= 1'b0;
            x     <= '0;
            y     <= '0;
            z     <= '0;
        end
        else
        begin
            valid <= got;
            if (got)
            begin
                x <= { data [39:32], data [47:40] };
                y <= { data [23:16], data [31:24] };
                z <= { data [7:0],   data [15:8]  };
            end
        end

endmodule
