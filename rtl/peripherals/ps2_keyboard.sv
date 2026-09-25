// PS/2 keyboard receiver: key events as USB HID usage codes.
//
// The keyboard clocks each byte out as an 11-bit frame (start 0, 8 data bits
// LSB first, odd parity, stop 1), data valid on the falling clock edge, at
// 10 - 16.7 kHz. A frame whose clock pauses for more than 100 us is dropped
// (it was cut off), and so is one with a bad start, parity or stop bit.
//
// Scan code set 2: a key sends its make code on press and F0 + the code on
// release; extended keys prefix E0. Pause sends E1 14 77 E1 F0 14 F0 77 (press
// only), which is swallowed; Print Screen's fake shift (E0 12) maps to no key.
// Each key event is one `valid` clock with `key` (HID usage, keyboard page) and
// `down` (1 pressed, 0 released). Codes with no HID usage give no event.

module ps2_keyboard
# (
    parameter clk_mhz = 50
)
(
    input              clk,
    input              rst,

    input              ps2_clk,
    input              ps2_data,

    output logic       valid,
    output logic [7:0] key,
    output logic       down
);

    // ---- the lines, synchronised; the falling clock edge ----------------------

    logic [2:0] clk_s;
    logic [1:0] dat_s;

    always_ff @ (posedge clk)
    begin
        clk_s <= { clk_s [1:0], ps2_clk  };
        dat_s <= { dat_s [0],   ps2_data };
    end

    wire fall = clk_s [2] & ~ clk_s [1];

    // ---- frames -------------------------------------------------------------

    localparam timeout = clk_mhz * 100;     // 100 us without a clock edge mid-frame
    localparam w_idle  = $clog2 (timeout + 1);

    logic [10:0]       shift;
    logic [3:0]        nbits;
    logic [w_idle-1:0] idle;
    logic              byte_ok;
    logic [7:0]        byte_in;

    always_ff @ (posedge clk)
        if (rst)
        begin
            nbits   <= '0;
            idle    <= '0;
            byte_ok <= 1'b0;
        end
        else
        begin
            byte_ok <= 1'b0;
            if (fall)
            begin
                idle  <= '0;
                shift <= { dat_s [1], shift [10:1] };
                if (nbits == 4'd10)
                begin
                    nbits <= '0;
                    // shift [0] start, [8:1] data, [9] parity; dat_s [1] is the stop bit
                    if (~ shift [1] & dat_s [1] & (^ shift [9:2] ^ shift [10]) == 1'b1)
                    begin
                        byte_ok <= 1'b1;
                        byte_in <= shift [9:2];
                    end
                end
                else
                    nbits <= nbits + 1'b1;
            end
            else if (nbits != 0)
            begin
                if (idle == w_idle' (timeout))
                    nbits <= '0;            // a cut-off frame
                else
                    idle <= idle + 1'b1;
            end
        end

    // ---- set 2 codes to HID usages --------------------------------------------

    function automatic logic [7:0] usage (input logic ext, input logic [7:0] code);
        usage = 8'h00;
        if (! ext)
            case (code)
                8'h1C: usage = 8'h04;  8'h32: usage = 8'h05;  8'h21: usage = 8'h06;  8'h23: usage = 8'h07;  // A B C D
                8'h24: usage = 8'h08;  8'h2B: usage = 8'h09;  8'h34: usage = 8'h0A;  8'h33: usage = 8'h0B;  // E F G H
                8'h43: usage = 8'h0C;  8'h3B: usage = 8'h0D;  8'h42: usage = 8'h0E;  8'h4B: usage = 8'h0F;  // I J K L
                8'h3A: usage = 8'h10;  8'h31: usage = 8'h11;  8'h44: usage = 8'h12;  8'h4D: usage = 8'h13;  // M N O P
                8'h15: usage = 8'h14;  8'h2D: usage = 8'h15;  8'h1B: usage = 8'h16;  8'h2C: usage = 8'h17;  // Q R S T
                8'h3C: usage = 8'h18;  8'h2A: usage = 8'h19;  8'h1D: usage = 8'h1A;  8'h22: usage = 8'h1B;  // U V W X
                8'h35: usage = 8'h1C;  8'h1A: usage = 8'h1D;                                              // Y Z
                8'h16: usage = 8'h1E;  8'h1E: usage = 8'h1F;  8'h26: usage = 8'h20;  8'h25: usage = 8'h21;  // 1 2 3 4
                8'h2E: usage = 8'h22;  8'h36: usage = 8'h23;  8'h3D: usage = 8'h24;  8'h3E: usage = 8'h25;  // 5 6 7 8
                8'h46: usage = 8'h26;  8'h45: usage = 8'h27;                                              // 9 0
                8'h5A: usage = 8'h28;  8'h76: usage = 8'h29;  8'h66: usage = 8'h2A;  8'h0D: usage = 8'h2B;  // Enter Esc Backspace Tab
                8'h29: usage = 8'h2C;  8'h4E: usage = 8'h2D;  8'h55: usage = 8'h2E;  8'h54: usage = 8'h2F;  // Space - = [
                8'h5B: usage = 8'h30;  8'h5D: usage = 8'h31;  8'h4C: usage = 8'h33;  8'h52: usage = 8'h34;  // ] \ ; '
                8'h0E: usage = 8'h35;  8'h41: usage = 8'h36;  8'h49: usage = 8'h37;  8'h4A: usage = 8'h38;  // ` , . /
                8'h58: usage = 8'h39;                                                                    // Caps Lock
                8'h05: usage = 8'h3A;  8'h06: usage = 8'h3B;  8'h04: usage = 8'h3C;  8'h0C: usage = 8'h3D;  // F1 - F4
                8'h03: usage = 8'h3E;  8'h0B: usage = 8'h3F;  8'h83: usage = 8'h40;  8'h0A: usage = 8'h41;  // F5 - F8
                8'h01: usage = 8'h42;  8'h09: usage = 8'h43;  8'h78: usage = 8'h44;  8'h07: usage = 8'h45;  // F9 - F12
                8'h7E: usage = 8'h47;  8'h77: usage = 8'h53;                                              // Scroll Lock, Num Lock
                8'h7C: usage = 8'h55;  8'h7B: usage = 8'h56;  8'h79: usage = 8'h57;                        // KP * - +
                8'h69: usage = 8'h59;  8'h72: usage = 8'h5A;  8'h7A: usage = 8'h5B;  8'h6B: usage = 8'h5C;  // KP 1 2 3 4
                8'h73: usage = 8'h5D;  8'h74: usage = 8'h5E;  8'h6C: usage = 8'h5F;  8'h75: usage = 8'h60;  // KP 5 6 7 8
                8'h7D: usage = 8'h61;  8'h70: usage = 8'h62;  8'h71: usage = 8'h63;                        // KP 9 0 .
                8'h14: usage = 8'hE0;  8'h12: usage = 8'hE1;  8'h11: usage = 8'hE2;  8'h59: usage = 8'hE5;  // LCtrl LShift LAlt RShift
                default: usage = 8'h00;
            endcase
        else
            case (code)
                8'h7C: usage = 8'h46;                                                                    // Print Screen
                8'h70: usage = 8'h49;  8'h6C: usage = 8'h4A;  8'h7D: usage = 8'h4B;  8'h71: usage = 8'h4C;  // Insert Home PgUp Delete
                8'h69: usage = 8'h4D;  8'h7A: usage = 8'h4E;                                              // End PgDn
                8'h74: usage = 8'h4F;  8'h6B: usage = 8'h50;  8'h72: usage = 8'h51;  8'h75: usage = 8'h52;  // Right Left Down Up
                8'h4A: usage = 8'h54;  8'h5A: usage = 8'h58;                                              // KP / KP Enter
                8'h2F: usage = 8'h65;                                                                    // Application
                8'h14: usage = 8'hE4;  8'h11: usage = 8'hE6;  8'h1F: usage = 8'hE3;  8'h27: usage = 8'hE7;  // RCtrl RAlt LGUI RGUI
                default: usage = 8'h00;                                                                  // E0 12: fake shift
            endcase
    endfunction

    // ---- bytes to events --------------------------------------------------------

    logic       ext, brk;
    logic [2:0] skip;               // bytes of a Pause sequence still to swallow

    always_ff @ (posedge clk)
        if (rst)
        begin
            valid <= 1'b0;
            key   <= '0;
            down  <= 1'b0;
            ext   <= 1'b0;
            brk   <= 1'b0;
            skip  <= '0;
        end
        else
        begin
            valid <= 1'b0;
            if (byte_ok)
            begin
                if (skip != 0)
                    skip <= skip - 1'b1;
                else if (byte_in == 8'hE1)
                    skip <= 3'd7;
                else if (byte_in == 8'hE0)
                    ext <= 1'b1;
                else if (byte_in == 8'hF0)
                    brk <= 1'b1;
                else
                begin
                    if (usage (ext, byte_in) != 8'h00)
                    begin
                        valid <= 1'b1;
                        key   <= usage (ext, byte_in);
                        down  <= ~ brk;
                    end
                    ext <= 1'b0;
                    brk <= 1'b0;
                end
            end
        end

endmodule
