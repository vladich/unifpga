// =============================================================================
// adxl345_axes — the six data registers of an ADXL345 (DATAX0..DATAZ1, read in
// one burst, the first byte in the top of `data`) as three axes in milli-g.
// Each axis is least significant byte first, 10 bits sign-extended to 16 at the
// power-on +-2 g range: one count is 3.90625 mg (256 counts per g), so milli-g
// = count * 125 / 32, exact. Shared by the I2C and the SPI readers.
// =============================================================================

module adxl345_axes
(
    input                      clk,
    input                      rst,
    input                      got,       // one clock: `data` is a new burst
    input        [47:0]        data,      // X0 X1 Y0 Y1 Z0 Z1
    output logic               valid,
    output logic signed [15:0] x,
    output logic signed [15:0] y,
    output logic signed [15:0] z
);

    function automatic logic signed [15:0] milli_g (input logic signed [15:0] count);
        logic signed [23:0] scaled;
        scaled  = count * 24'sd125;
        milli_g = 16' (scaled >>> 5);
    endfunction

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
                x <= milli_g ({ data [39:32], data [47:40] });
                y <= milli_g ({ data [23:16], data [31:24] });
                z <= milli_g ({ data [7:0],   data [15:8]  });
            end
        end

endmodule
