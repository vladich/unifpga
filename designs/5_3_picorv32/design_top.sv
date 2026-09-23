// =============================================================================
// 5_3_picorv32
// =============================================================================
//
// requires:
//   buttons >= 1
//   seven_segment >= 1

module design_top
# (
    parameter int clk_mhz       = 50,
                  w_sw          = 0,
                  w_btn         = 0,
                  w_led         = 0,
                  w_digit       = 0,
                  w_rgb_led     = 0,
                  screen_width  = 0,
                  screen_height = 0,
                  w_red         = 0,
                  w_green       = 0,
                  w_blue        = 0,
                  w_gpio        = 0,
                  w_x = (screen_width  > 0) ? $clog2(screen_width ) : 1,
                  w_y = (screen_height > 0) ? $clog2(screen_height) : 1
)
(
    input                            clk,
    input                            rst,
    input        [w_sw     - 1 : 0]  sw,
    input        [w_btn    - 1 : 0]  btn,
    output logic [w_led    - 1 : 0]  led,
    output logic [          7 : 0]   abcdefgh,
    output logic [w_digit  - 1 : 0]  digit,
    output logic [w_rgb_led- 1 : 0]  rgb_r,
    output logic [w_rgb_led- 1 : 0]  rgb_g,
    output logic [w_rgb_led- 1 : 0]  rgb_b,
    input        [w_x      - 1 : 0]  x,
    input        [w_y      - 1 : 0]  y,
    output logic [w_red    - 1 : 0]  red,
    output logic [w_green  - 1 : 0]  green,
    output logic [w_blue   - 1 : 0]  blue,
    input        [         23 : 0]   mic_sample,
    input                            mic_valid,
    output logic [         15 : 0]   sound,
    input                            uart_rx,
    output logic                     uart_tx,
    inout        [w_gpio   - 1 : 0]  gpio
);

    // ---- slow_clk derivation --------------------------------------------------
    // design_top has no `slow_clk` port, so derive a ~1 Hz tick from
    // the system clock.
    // clk_mhz <= 1 is the testbench setting (tb.sv passes clk as slow_clk):
    // a two-bit divider keeps the simulation short.
    localparam int W_SLOW_CLK_DIV = (clk_mhz > 1) ? $clog2(clk_mhz * 1_000_000) : 2;
    logic [W_SLOW_CLK_DIV - 1 : 0] slow_clk_div;
    logic                          slow_clk;
    always_ff @(posedge clk or posedge rst)
        if (rst) slow_clk_div <= '0;
        else     slow_clk_div <= slow_clk_div + 1'b1;
    assign slow_clk = slow_clk_div[W_SLOW_CLK_DIV - 1];



    //------------------------------------------------------------------------

    // assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
    assign red        = '0;
    assign green      = '0;
    assign blue       = '0;
    assign sound      = '0;
    assign uart_tx    = '1;

    //------------------------------------------------------------------------
    wire                trap;
    wire                mem_valid;
    wire                mem_instr;
    reg                 mem_ready;
    wire [31:0]         mem_addr;
    wire [31:0]         mem_wdata;
    wire [3:0]          mem_wstrb;
    reg  [31:0]         mem_rdata;


    picorv32 picorv (
        .clk        ( slow_clk  ),
        .resetn     ( ~rst      ),
        .trap       ( trap      ),
        .mem_valid  ( mem_valid ),
        .mem_instr  ( mem_instr ),
        .mem_ready  ( mem_ready ),
        .mem_addr   ( mem_addr  ),
        .mem_wdata  ( mem_wdata ),
        .mem_wstrb  ( mem_wstrb ),
        .mem_rdata  ( mem_rdata )
    );

    instruction_ram memory_file (
        .clk        ( slow_clk  ),
        .mem_valid  ( mem_valid ),
        .mem_ready  ( mem_ready ),
        .mem_addr   ( mem_addr  ),
        .mem_wstrb  ( mem_wstrb ),
        .mem_rdata  ( mem_rdata ),
        .mem_wdata  ( mem_wdata )
    );

    logic [w_led-1:0] led_reg;

    // Bind LEDs into 128 with bit shift 0..w_led
    always_ff @(posedge clk) begin
        if (rst) begin
            led_reg <= '0;
        end
        else if (mem_addr == 32'h10010100 && mem_valid) begin
            led_reg <= mem_wdata [0 +: w_led];
        end
    end

    assign led[w_led-1]   = mem_valid;
    assign led[w_led-2]   = mem_ready;
    assign led[w_led-3]   = trap;
    assign led[w_led-4:0] = led_reg;

    seven_segment_display
    # (
        .w_digit  ( w_digit * 4 ),
        .clk_mhz  ( clk_mhz  )
    )
    display
    (
        .clk      ( clk      ),
        .rst      ( rst      ),

        .number   ( ~btn[0] ? (mem_addr >> 2) : mem_rdata ),
        .dots     ( '0       ),

        .abcdefgh ( abcdefgh ),
        .digit    ( digit    )
    );
endmodule

