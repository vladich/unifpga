// A test of the board's RAM: fills its words (2^w_mem_addr of them, at most a
// million per pass) with a pattern made from each address, reads them back and
// compares, then again with the pattern inverted, and so on.
//
//   led [0]   toggles at the end of each pass
//   led [1]   on while every word read back so far compared equal
//   led [2]   on once a word differed (stays on)
//   led [3..] the count of differing words, its low bits
//
// requires:
//   memory
//   leds >= 3

module design_top
`include "design_top_interface.svh"

    assign abcdefgh = '0;
    assign digit    = '0;
    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign red      = '0;
    assign green    = '0;
    assign blue     = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    // ---- the pass: write every word, then read every word ---------------------

    localparam int W_ADDR = (w_mem_addr < 20) ? w_mem_addr : 20;   // address bits a pass covers
    localparam int W_FULL = (w_mem_addr > 0) ? w_mem_addr : 1;
    localparam int W_DATA = (w_mem_data > 0 && w_mem_data <= 32) ? w_mem_data : 32;

    typedef enum logic [1:0] { S_WRITE, S_WRITE_ACK, S_READ, S_READ_ACK } state_t;

    state_t               state;
    logic [W_FULL-1:0]    a;              // the word of this pass (its low W_ADDR bits count)
    logic                 inverted;       // this pass's pattern
    logic                 passes;         // led [0]
    logic                 failed;
    logic [15:0]          errors;

    // the pattern of a word: its address and the address's complement, spread
    // over the word, inverted every other pass
    wire [31:0]        pattern32 = { ~ a [15:0], a [15:0] } ^ { 32 { inverted } };
    wire [W_DATA-1:0]  pattern   = pattern32 [W_DATA-1:0];
    wire               last      = & a [W_ADDR-1:0];             // the pass's last word

    assign mem_addr  = a;
    assign mem_wdata = pattern;
    assign mem_be    = '1;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state    <= S_WRITE;
            a        <= '0;
            inverted <= 1'b0;
            passes   <= 1'b0;
            failed   <= 1'b0;
            errors   <= '0;
            mem_req  <= 1'b0;
            mem_we   <= 1'b0;
        end
        else
        begin
            mem_req <= 1'b0;

            case (state)

            S_WRITE:                                 // request the write of word a
                if (mem_ready && ! mem_req)
                begin
                    mem_req <= 1'b1;
                    mem_we  <= 1'b1;
                    state   <= S_WRITE_ACK;
                end

            S_WRITE_ACK:
                if (mem_ack)
                begin
                    a     <= last ? '0 : a + 1'b1;
                    state <= last ? S_READ : S_WRITE;
                end

            S_READ:                                  // request the read of word a
                if (mem_ready && ! mem_req)
                begin
                    mem_req <= 1'b1;
                    mem_we  <= 1'b0;
                    state   <= S_READ_ACK;
                end

            S_READ_ACK:
                if (mem_ack)
                begin
                    if (mem_rdata != pattern)
                    begin
                        failed <= 1'b1;
                        if (errors != '1)
                            errors <= errors + 1'b1;
                    end
                    a <= last ? '0 : a + 1'b1;
                    if (last)
                    begin                            // the pass is over
                        inverted <= ~ inverted;
                        passes   <= ~ passes;
                        state    <= S_WRITE;
                    end
                    else
                        state <= S_READ;
                end

            default:
                state <= S_WRITE;

            endcase
        end

    // ---- the LEDs ----------------------------------------------------------------

    always_comb
    begin
        led = '0;
        if (w_led > 0) led [0] = passes;
        if (w_led > 1) led [1] = ~ failed;
        if (w_led > 2) led [2] = failed;
        for (int i = 3; i < w_led; i++)
            led [i] = errors [i - 3];
    end

endmodule
