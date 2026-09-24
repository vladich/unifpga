// SSD1306 OLED panel over 4-wire SPI (Digilent PmodOLED, the 128x32 OLEDs
// of Genesys 2 / Nexys Video / ZedBoard, 128x64 breakouts).
//
// Powers the panel up in the order the SSD1306 datasheet and the PmodOLED
// driver give (VDD, display off, reset pulse, charge pump and pre-charge,
// VBAT, 100 ms; Digilent vivado-library PmodOLED OledDriver.c), sets horizontal addressing over the whole panel,
// then streams the frame for ever: for every 8-row page and column it
// presents the eight pixel positions (x, y), holds each two clocks and reads
// `pixel` (bit 0) on the second, so the design may answer combinationally or
// one clock late, and sends the byte (D0 = top row of the page).
// Every frame starts by setting the column and page window again, so a
// glitch on the wires costs one frame at most.
//
// SPI mode 3 (SCLK idles high, data changes on the falling edge, the
// controller samples on the rising edge), at most SPI_KHZ (SSD1306: 10 MHz).

module ssd1306_spi_oled
# (
    parameter int CLK_MHZ = 50,
    parameter int WIDTH   = 128,
    parameter int HEIGHT  = 32,
    parameter int W_PIXEL = 1,
    parameter int SPI_KHZ = 4000,
    parameter int COM_PINS = 'h20,     // DA argument: 'h20 on Digilent's UG-2832 panels, 'h12 on most 128x64
    localparam int W_X    = WIDTH  > 1 ? $clog2(WIDTH)  : 1,
    localparam int W_Y    = HEIGHT > 1 ? $clog2(HEIGHT) : 1
)
(
    input                        clk,
    input                        rst,

    output logic [W_X     - 1:0] x,
    output logic [W_Y     - 1:0] y,
    input        [W_PIXEL - 1:0] pixel,

    output logic                 cs_n,
    output logic                 sclk,
    output logic                 sdin,
    output logic                 dc,       // 0 = command, 1 = display data
    output logic                 res_n,
    output logic                 vbat_n,   // PmodOLED VBATC: 0 = VBAT on
    output logic                 vdd_n     // PmodOLED VDDC:  0 = VDD on
);

    localparam int HALF     = (CLK_MHZ * 1000) / (2 * SPI_KHZ) > 0 ? (CLK_MHZ * 1000) / (2 * SPI_KHZ) : 1;
    localparam int CYC_MS   = CLK_MHZ * 1000;
    localparam int PAGES    = HEIGHT / 8;

    // ---- power-up and window program ----------------------------------------
    // {kind, value}: kind 0 = command byte, 1 = wait value ms,
    // 2 = set {vdd_n, vbat_n, res_n} = value[2:0], 3 = end (stream the frame)
    localparam int FRAME_STEP = 23;          // the window commands, before every frame

    function automatic [9:0] step (input [5:0] i);
        case (i)
             0: step = {2'd2, 8'b011};       // VDD on
             1: step = {2'd1, 8'd1};
             2: step = {2'd0, 8'hAE};        // display off
             3: step = {2'd2, 8'b010};       // reset low
             4: step = {2'd1, 8'd1};
             5: step = {2'd2, 8'b011};       // reset high
             6: step = {2'd1, 8'd1};
             7: step = {2'd0, 8'h8D};        // charge pump
             8: step = {2'd0, 8'h14};
             9: step = {2'd0, 8'hD9};        // pre-charge period
            10: step = {2'd0, 8'hF1};
            11: step = {2'd2, 8'b001};       // VBAT on
            12: step = {2'd1, 8'd100};
            13: step = {2'd0, 8'h81};        // contrast
            14: step = {2'd0, 8'h0F};
            15: step = {2'd0, 8'hA1};        // segment remap: column 0 on the left
            16: step = {2'd0, 8'hC8};        // COM scan from the top
            17: step = {2'd0, 8'hDA};        // COM pins
            18: step = {2'd0, 8'(COM_PINS)};
            19: step = {2'd0, 8'hA8};        // multiplex ratio
            20: step = {2'd0, 8'(HEIGHT - 1)};
            21: step = {2'd0, 8'h20};        // horizontal addressing
            22: step = {2'd0, 8'h00};
            23: step = {2'd0, 8'h21};        // columns 0 .. WIDTH - 1
            24: step = {2'd0, 8'h00};
            25: step = {2'd0, 8'(WIDTH - 1)};
            26: step = {2'd0, 8'h22};        // pages 0 .. PAGES - 1
            27: step = {2'd0, 8'h00};
            28: step = {2'd0, 8'(PAGES - 1)};
            29: step = {2'd0, 8'hAF};        // display on
            default: step = {2'd3, 8'h00};
        endcase
    endfunction

    typedef enum logic [2:0] { S_STEP, S_WAIT, S_SEND, S_FETCH } state_t;
    state_t state;

    logic [5:0]  idx;
    wire  [9:0]  cur = step (idx);
    logic        streaming;               // the byte in flight is display data
    logic [7:0]  shreg;
    logic [2:0]  bitc;
    logic [$clog2(HALF + 1) - 1:0]   hc;
    logic [$clog2(CYC_MS + 1) - 1:0] ms_cyc;
    logic [7:0]  ms_left;
    logic [2:0]  fk;                      // pixel of the byte being fetched
    logic        ph;                      // 0: position presented, 1: read it
    logic [6:0]  got;                     // pixels 0..6 of the byte
    logic [W_X - 1:0] col;
    logic [W_Y - 1:0] page_row;           // page * 8

    always_ff @ (posedge clk)
        if (rst)
        begin
            state     <= S_STEP;
            idx       <= '0;
            streaming <= 1'b0;
            cs_n      <= 1'b1;
            sclk      <= 1'b1;
            sdin      <= 1'b0;
            dc        <= 1'b0;
            res_n     <= 1'b1;
            vbat_n    <= 1'b1;
            vdd_n     <= 1'b1;
            hc        <= '0;
            bitc      <= '0;
            fk        <= '0;
            ph        <= 1'b0;
            col       <= '0;
            page_row  <= '0;
            x         <= '0;
            y         <= '0;
        end
        else
        begin
            case (state)
            S_STEP:
                case (cur [9:8])
                2'd0:
                begin
                    dc        <= 1'b0;
                    cs_n      <= 1'b0;
                    shreg     <= cur [7:0];
                    streaming <= 1'b0;
                    bitc      <= '0;
                    hc        <= '0;
                    state     <= S_SEND;
                end
                2'd1:
                begin
                    ms_left <= cur [7:0];
                    ms_cyc  <= '0;
                    state   <= S_WAIT;
                end
                2'd2:
                begin
                    { vdd_n, vbat_n, res_n } <= cur [2:0];
                    idx <= idx + 1'd1;
                end
                default:                        // the frame
                begin
                    col      <= '0;
                    page_row <= '0;
                    x        <= '0;
                    y        <= '0;
                    fk       <= '0;
                    ph       <= 1'b0;
                    state    <= S_FETCH;
                end
                endcase

            S_WAIT:
                if (ms_left == 0)
                begin
                    idx   <= idx + 1'd1;
                    state <= S_STEP;
                end
                else if (ms_cyc == CYC_MS - 1)
                begin
                    ms_cyc  <= '0;
                    ms_left <= ms_left - 1'd1;
                end
                else
                    ms_cyc <= ms_cyc + 1'd1;

            // pixel fk of the byte: its position held a clock, then read
            S_FETCH:
                if (!ph)
                    ph <= 1'b1;
                else if (fk == 3'd7)
                begin
                    dc        <= 1'b1;
                    cs_n      <= 1'b0;
                    shreg     <= { pixel [0], got };
                    streaming <= 1'b1;
                    bitc      <= '0;
                    hc        <= '0;
                    state     <= S_SEND;
                end
                else
                begin
                    got [fk] <= pixel [0];
                    y        <= page_row + W_Y'(fk) + 1'd1;
                    fk       <= fk + 1'd1;
                    ph       <= 1'b0;
                end

            S_SEND:
                if (hc != HALF - 1)
                    hc <= hc + 1'd1;
                else
                begin
                    hc <= '0;
                    if (sclk)
                    begin
                        sclk <= 1'b0;             // falling edge: the next bit out
                        sdin <= shreg [7];
                    end
                    else
                    begin
                        sclk  <= 1'b1;            // rising edge: the panel samples
                        shreg <= shreg << 1;
                        bitc  <= bitc + 1'd1;
                        if (bitc == 3'd7)
                        begin
                            if (!streaming)
                            begin
                                idx   <= idx + 1'd1;
                                state <= S_STEP;
                            end
                            else if (col == W_X'(WIDTH - 1) && page_row == W_Y'((PAGES - 1) * 8))
                            begin
                                idx   <= 6'(FRAME_STEP); // the next frame: the window again
                                state <= S_STEP;
                            end
                            else
                            begin
                                if (col == W_X'(WIDTH - 1))
                                begin
                                    col      <= '0;
                                    page_row <= page_row + 4'd8;
                                    x        <= '0;
                                    y        <= page_row + 4'd8;
                                end
                                else
                                begin
                                    col <= col + 1'd1;
                                    x   <= col + 1'd1;
                                    y   <= page_row;
                                end
                                fk    <= '0;
                                ph    <= 1'b0;
                                state <= S_FETCH;
                            end
                        end
                    end
                end

            default:
                state <= S_STEP;
            endcase
        end

endmodule
