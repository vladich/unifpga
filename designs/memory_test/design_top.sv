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
# (
    // ---- Clock & reset (always present) -------------------------------------
    parameter int clk_mhz       = 50,    // Advertised frequency of `clk` in MHz

    // ---- User input banks (concatenated across providers) -------------------
    parameter int w_sw          = 0,     // Width of slide-switch bus
    parameter int w_btn         = 0,     // Width of push-button bus

    // ---- User output banks --------------------------------------------------
    parameter int w_led         = 0,     // Width of single-colour LED bus
    parameter int w_digit       = 0,     // Number of 7-segment digit positions
    parameter int w_rgb_led     = 0,     // Number of RGB LEDs

    // ---- Screen (raster-scan pixel display; 0×0 = no screen) ---------------
    parameter int screen_width  = 0,
    parameter int screen_height = 0,
    parameter int w_red         = 0,     // Bits per RED channel
    parameter int w_green       = 0,     // Bits per GREEN channel
    parameter int w_blue        = 0,     // Bits per BLUE channel

    // ---- GPIO ---------------------------------------------------------------
    parameter int w_gpio        = 0,     // Generic bidirectional pin bank

    // ---- Memory (optional) --------------------------------------------------
    parameter int w_mem_addr    = 0,     // The board's RAM: word address bits
    parameter int mem_bytes     = 0,     // Bytes per memory word

    // ---- Derived widths (do not override) -----------------------------------
    parameter int w_x = (screen_width  > 0) ? $clog2(screen_width ) : 1,
    parameter int w_y = (screen_height > 0) ? $clog2(screen_height) : 1,
    parameter int w_mem_data = mem_bytes * 8
)
(
    // ---- Clock & reset ------------------------------------------------------
    input                            clk,
    input                            rst,         // active-high

    // ---- Switches / buttons (active-high — codegen normalizes polarity) ----
    input        [w_sw     - 1 : 0]  sw,
    input        [w_btn    - 1 : 0]  btn,

    // ---- LEDs (active-high) -------------------------------------------------
    output logic [w_led    - 1 : 0]  led,

    // ---- 7-segment display (shared segment lines + digit-select) -----------
    output logic [          7 : 0]   abcdefgh,
    output logic [w_digit  - 1 : 0]  digit,

    // ---- RGB LEDs (active-high) ---------------------------------------------
    output logic [w_rgb_led- 1 : 0]  rgb_r,
    output logic [w_rgb_led- 1 : 0]  rgb_g,
    output logic [w_rgb_led- 1 : 0]  rgb_b,

    // ---- Pixel display ------------------------------------------------------
    input        [w_x      - 1 : 0]  x,
    input        [w_y      - 1 : 0]  y,
    output logic [w_red    - 1 : 0]  red,
    output logic [w_green  - 1 : 0]  green,
    output logic [w_blue   - 1 : 0]  blue,

    // ---- Audio in / out -----------------------------------------------------
    input        [         23 : 0]   mic_sample,
    input                            mic_valid,
    output logic [         15 : 0]   sound,

    // ---- Serial console (raw 2-wire UART) -----------------------------------
    input                            uart_rx,
    output logic                     uart_tx,

    // ---- General-purpose I/O ------------------------------------------------
    inout        [w_gpio   - 1 : 0]  gpio,

    // ---- Memory (optional): the board's RAM, 2^w_mem_addr words of mem_bytes
    // bytes; a request while mem_ready, mem_ack one clock when done -----------
    output logic                     mem_req,
    output logic                     mem_we,
    output logic [w_mem_addr - 1 : 0] mem_addr,
    output logic [w_mem_data - 1 : 0] mem_wdata,
    output logic [mem_bytes  - 1 : 0] mem_be,
    input                            mem_ready,
    input                            mem_ack,
    input        [w_mem_data - 1 : 0] mem_rdata
);

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
