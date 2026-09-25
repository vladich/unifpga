// =============================================================================
// i2c_temperature_reader — polls an LM75-family I2C temperature sensor (LM75,
// LM75A, ADT7420, TMP75 ...) and gives its reading in 1/16 degrees Celsius.
//
// Every POLL_MS: register 0 (the temperature register), two bytes, most
// significant first, through i2c_reg_master. The reading is two's complement,
// left-aligned in the 16 bits: SHIFT is where its 1/16 C bit sits (the
// ADT7420's 13-bit reading has 1/16 C at bit 3: SHIFT 3; the LM75's 9 bits
// (1/2 C) and the LM75A's 11 bits (1/8 C) end at bit 7 / bit 5, so 1/16 C is
// bit 4: SHIFT 4). `temp` = reading >>> SHIFT, exact for all.
//
// The sensor's address (its A0..A2 straps) need not be known: the master
// tries the family's 0x48..0x4F until one answers.
// =============================================================================

module i2c_temperature_reader
# (
    parameter CLK_MHZ = 50,
    parameter SCL_KHZ = 100,
    parameter SHIFT   = 4,
    parameter POLL_MS = 250
)
(
    input                      clk,
    input                      rst,
    output                     scl,
    inout                      sda,
    output logic               valid,     // one clock: a new reading
    output logic signed [15:0] temp       // 1/16 degrees Celsius
);

    wire        got;
    wire [15:0] raw;

    i2c_reg_master
    # (
        .CLK_MHZ (CLK_MHZ), .SCL_KHZ (SCL_KHZ), .POLL_MS (POLL_MS),
        .N_ADDR  (8),
        .ADDRS   ({ 7'h48, 7'h49, 7'h4A, 7'h4B, 7'h4C, 7'h4D, 7'h4E, 7'h4F }),
        .REG     (8'h00),
        .N_BYTES (2)
    )
    i_master
    (
        .clk (clk), .rst (rst), .scl (scl), .sda (sda),
        .valid (got), .data (raw), .found ()
    );

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            valid <= 1'b0;
            temp  <= '0;
        end
        else
        begin
            valid <= got;
            if (got)
                temp <= $signed (raw) >>> SHIFT;
        end

endmodule
