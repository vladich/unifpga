// HD44780-compatible character LCD (16x2, 20x4 ...) on its 4-bit bus,
// write-only (R/W tied low by the peripheral).
//
// Initialises the controller by instruction, as the HD44780 datasheet's
// 4-bit procedure gives (after 50 ms: 0x3, 0x3, 0x3, 0x2 as nibbles, then
// function set, display off, clear, entry mode, display on), then rewrites
// the panel for ever: for each row the DDRAM address of its first column,
// then every column's character. A character's position (column, row) is
// held two clocks and `char` read on the second, so the design may answer
// combinationally or one clock late.
//
// Timing is counted in clocks of CLK_MHZ: 1 us set-up, enable pulse and
// hold around every nibble, 50 us after a byte (37 us in the datasheet),
// 2 ms after clear (1.52 ms).

module hd44780_lcd
# (
    parameter int CLK_MHZ = 50,
    parameter int COLUMNS = 16,
    parameter int ROWS    = 2,
    localparam int W_COL  = COLUMNS > 1 ? $clog2(COLUMNS) : 1,
    localparam int W_ROW  = ROWS    > 1 ? $clog2(ROWS)    : 1
)
(
    input                      clk,
    input                      rst,

    output logic [W_COL - 1:0] column,
    output logic [W_ROW - 1:0] row,
    input        [        7:0] char,

    output logic               rs,     // 0 = instruction, 1 = data
    output logic               e,
    output logic [        3:0] d       // D7..D4 as d[3:0]
);

    localparam int CYC_US = CLK_MHZ > 0 ? CLK_MHZ : 1;

    // {kind, value, wait_us}: kind 0 = one nibble (value[3:0]), 1 = an
    // instruction byte, 2 = end (refresh the panel)
    function automatic [25:0] step (input [3:0] i);
        case (i)
            0: step = {2'd0, 8'h03, 16'd5000};
            1: step = {2'd0, 8'h03, 16'd200};
            2: step = {2'd0, 8'h03, 16'd200};
            3: step = {2'd0, 8'h02, 16'd200};            // 4-bit bus from here on
            4: step = {2'd1, ROWS > 1 ? 8'h28 : 8'h20, 16'd50};
            5: step = {2'd1, 8'h08, 16'd50};             // display off
            6: step = {2'd1, 8'h01, 16'd2000};           // clear
            7: step = {2'd1, 8'h06, 16'd50};             // entry mode: increment
            8: step = {2'd1, 8'h0C, 16'd50};             // display on, no cursor
            default: step = {2'd2, 8'h00, 16'd0};
        endcase
    endfunction

    // DDRAM address of a row's first column (rows 2 and 3 continue rows 0 and 1)
    function automatic [6:0] row_address (input [W_ROW - 1:0] r);
        case (r % 4)
            0:       row_address = 7'h00;
            1:       row_address = 7'h40;
            2:       row_address = 7'(COLUMNS);
            default: row_address = 7'(8'h40 + COLUMNS);
        endcase
    endfunction

    typedef enum logic [2:0] { S_POWER, S_STEP, S_NIBBLE, S_WAIT, S_FETCH } state_t;
    state_t state, after;

    logic [3:0]  idx;
    wire  [25:0] cur = step (idx);
    logic [7:0]  byte_out;
    logic        two;          // the byte's high nibble is out, the low one next
    logic [1:0]  phase;        // nibble: 0 set-up, 1 enable high, 2 hold
    logic [15:0] wait_us;      // after the byte
    logic [$clog2(CYC_US + 1) - 1:0] cyc;
    logic [15:0] us;
    logic        address_sent; // this row's DDRAM address is set
    logic        ph;
    wire  [6:0]  address = row_address (row);

    always_ff @ (posedge clk)
        if (rst)
        begin
            state        <= S_POWER;
            idx          <= '0;
            rs           <= 1'b0;
            e            <= 1'b0;
            d            <= '0;
            cyc          <= '0;
            us           <= '0;
            address_sent <= 1'b0;
            column       <= '0;
            row          <= '0;
            ph           <= 1'b0;
        end
        else
        begin
            case (state)
            S_POWER:                                  // 50 ms after power-up
                if (cyc != CYC_US - 1)
                    cyc <= cyc + 1'd1;
                else
                begin
                    cyc <= '0;
                    us  <= us + 1'd1;
                    if (us == 16'd50000)
                    begin
                        us    <= '0;
                        state <= S_STEP;
                    end
                end

            S_STEP:
                case (cur [25:24])
                2'd0, 2'd1:
                begin
                    rs       <= 1'b0;
                    byte_out <= cur [23:16];
                    two      <= cur [25:24] == 2'd1;
                    d        <= cur [25:24] == 2'd1 ? cur [23:20] : cur [19:16];
                    wait_us  <= cur [15:0];
                    phase    <= 2'd0;
                    cyc      <= '0;
                    us       <= '0;
                    idx      <= idx + 1'd1;
                    after    <= S_STEP;
                    state    <= S_NIBBLE;
                end
                default:
                begin
                    address_sent <= 1'b0;
                    column       <= '0;
                    row          <= '0;
                    ph           <= 1'b0;
                    state        <= S_FETCH;
                end
                endcase

            // one nibble on d: 1 us set-up, 1 us enable, 1 us hold
            S_NIBBLE:
                if (cyc != CYC_US - 1)
                    cyc <= cyc + 1'd1;
                else
                begin
                    cyc <= '0;
                    case (phase)
                    2'd0: begin e <= 1'b1; phase <= 2'd1; end
                    2'd1: begin e <= 1'b0; phase <= 2'd2; end
                    default:
                        if (two)
                        begin
                            two   <= 1'b0;
                            d     <= byte_out [3:0];
                            phase <= 2'd0;
                        end
                        else
                        begin
                            us    <= '0;
                            state <= S_WAIT;
                        end
                    endcase
                end

            S_WAIT:
                if (us == wait_us)
                    state <= after;
                else if (cyc != CYC_US - 1)
                    cyc <= cyc + 1'd1;
                else
                begin
                    cyc <= '0;
                    us  <= us + 1'd1;
                end

            // refresh: the row's address, then (column, row) held two clocks and read
            S_FETCH:
                if (!address_sent)
                begin
                    rs           <= 1'b0;
                    byte_out     <= { 1'b1, address };
                    d            <= { 1'b1, address [6:4] };
                    two          <= 1'b1;
                    wait_us      <= 16'd50;
                    phase        <= 2'd0;
                    cyc          <= '0;
                    address_sent <= 1'b1;
                    after        <= S_FETCH;
                    state        <= S_NIBBLE;
                end
                else if (!ph)
                    ph <= 1'b1;
                else
                begin
                    rs       <= 1'b1;
                    byte_out <= char;
                    d        <= char [7:4];
                    two      <= 1'b1;
                    wait_us  <= 16'd50;
                    phase    <= 2'd0;
                    cyc      <= '0;
                    ph       <= 1'b0;
                    after    <= S_FETCH;
                    state    <= S_NIBBLE;
                    if (column == W_COL'(COLUMNS - 1))
                    begin
                        column       <= '0;
                        row          <= row == W_ROW'(ROWS - 1) ? '0 : row + 1'd1;
                        address_sent <= 1'b0;
                    end
                    else
                        column <= column + 1'd1;
                end

            default:
                state <= S_STEP;
            endcase
        end

endmodule
