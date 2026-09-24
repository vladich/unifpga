// =============================================================================
// 1_02_mux
// =============================================================================
//
// requires:
//   switches >= 3 if !(w_btn >= 3)

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


    //------------------------------------------------------------------------

    // assign led        = '0;
       assign abcdefgh   = '0;
       assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    wire sel, a, b;

    generate
        if (w_btn >= 3)
        begin : use_keys
            assign sel = btn [2];
            assign a   = btn [1];
            assign b   = btn [0];
        end
        else
        begin : use_switches
            assign sel = sw  [2];
            assign a   = sw  [1];
            assign b   = sw  [0];
        end
    endgenerate

    //------------------------------------------------------------------------

    // Five different implementations

    wire mux0 = sel ? a : b;

    //------------------------------------------------------------------------

    wire [1:0] ab = { a, b };
    wire mux1 = ab [sel];

    //------------------------------------------------------------------------

    logic mux2;

    always_comb
        if (sel)
            mux2 = a;
        else
            mux2 = b;

    //------------------------------------------------------------------------

    logic mux3;

    always_comb
        case (sel)
        1'b1: mux3 = a;
        1'b0: mux3 = b;
        endcase

    //------------------------------------------------------------------------

    // Exercise 1: Implement mux
    // without using "?" operation, "if", "case" or a bit selection.
    // Use only operations "&", "|", "~" and parenthesis, "(" and ")".

    wire mux4 = 1'b0;

    //------------------------------------------------------------------------

    // Use table

    wire [0:7] table5 =
    {
        1'b0, // sel = 0, a = 0, b = 0
        1'b1, // sel = 0, a = 0, b = 1
        1'b0, // sel = 0, a = 1, b = 0
        1'b1, // sel = 0, a = 1, b = 1
        1'b0, // sel = 1, a = 0, b = 0
        1'b0, // sel = 1, a = 0, b = 1
        1'b1, // sel = 1, a = 1, b = 0
        1'b1  // sel = 1, a = 1, b = 1
    };

    wire mux5 = table5 [{ sel, a, b }];

    // Exercise 2: Change the table to get the correct result by doing
    // wire mux5_2 = table5_2 [{ a, b, sel }];

    //------------------------------------------------------------------------

    wire [7:0] table6 =
    {
        1'b1, // sel = 1, a = 1, b = 1
        1'b1, // sel = 1, a = 1, b = 0
        1'b0, // sel = 1, a = 0, b = 1
        1'b0, // sel = 1, a = 0, b = 0
        1'b1, // sel = 0, a = 1, b = 1
        1'b0, // sel = 0, a = 1, b = 0
        1'b1, // sel = 0, a = 0, b = 1
        1'b0  // sel = 0, a = 0, b = 0
    };

    wire mux6 = table6 [{ sel, a, b }];

    //------------------------------------------------------------------------

    wire [7:0] table7 = 8'b1100_1010;
    wire mux7 = table7 [{ sel, a, b }];

    //------------------------------------------------------------------------

    `ifdef __ICARUS__

    // The syntax below does not work with Icarus Verilog
    wire mux8 = mux0;

    `elsif YOSYS

    wire mux8 = mux0;

    `else

    wire [0:1][0:1][0:1] table8 =
    {
        1'b0, // sel = 0, a = 0, b = 0
        1'b1, // sel = 0, a = 0, b = 1
        1'b0, // sel = 0, a = 1, b = 0
        1'b1, // sel = 0, a = 1, b = 1
        1'b0, // sel = 1, a = 0, b = 0
        1'b0, // sel = 1, a = 0, b = 1
        1'b1, // sel = 1, a = 1, b = 0
        1'b1  // sel = 1, a = 1, b = 1
    };

    wire mux8 = table8 [sel][a][b];

    `endif

    //------------------------------------------------------------------------

    `ifdef __ICARUS__

    // The syntax below does not work with Icarus Verilog
    wire mux9 = mux0;

    `elsif YOSYS

    wire mux9 = mux0;

    `else

    wire [1:0][1:0][1:0] table9 =
    {
        1'b1, // sel = 1, a = 1, b = 1
        1'b1, // sel = 1, a = 1, b = 0
        1'b0, // sel = 1, a = 0, b = 1
        1'b0, // sel = 1, a = 0, b = 0
        1'b1, // sel = 0, a = 1, b = 1
        1'b0, // sel = 0, a = 1, b = 0
        1'b1, // sel = 0, a = 0, b = 1
        1'b0  // sel = 0, a = 0, b = 0
    };

    wire mux9 = table9 [sel][a][b];

    `endif

    //------------------------------------------------------------------------

    `ifdef SYNOPSYS_CADENCE_MENTOR

    // This syntax probably works only with Synopsys VCS, Cadence Xselium
    // and QuestaSim from Siemens EDA (former Mentor Graphics)

    wire [0:1][0:1][0:1] table10 =
    '{
        '{
            '{ 1'b0, 1'b0 },  // a = 0, b = 0, sel = 0/1
            '{ 1'b1, 1'b0 }   // a = 0, b = 1, sel = 0/1
        },

        '{
            '{ 1'b0, 1'b1 },  // a = 1, b = 0, sel = 0/1
            '{ 1'b1, 1'b1 }   // a = 1, b = 1, sel = 0/1
        }
    };

    wire mux10 = table10 [a][b][sel];

    `else

    wire mux10 = mux0;

    `endif

    //------------------------------------------------------------------------

    `ifdef SYNOPSYS_CADENCE_MENTOR

    // This syntax probably works only with Synopsys VCS, Cadence Xselium
    // and QuestaSim from Siemens EDA (former Mentor Graphics)

    logic table11 [0:1][0:1][0:1] =
    '{
        '{
            '{ 1'b0, 1'b0 },  // a = 0, b = 0, sel = 0/1
            '{ 1'b1, 1'b0 }   // a = 0, b = 1, sel = 0/1
        },

        '{
            '{ 1'b0, 1'b1 },  // a = 1, b = 0, sel = 0/1
            '{ 1'b1, 1'b1 }   // a = 1, b = 1, sel = 0/1
        }
    };

    wire mux11 = table11 [a][b][sel];

    // Exercise 3: Change the table to get the correct result by doing
    // wire mux11_2 = table11_2 [sel][b][a];

    `else

    wire mux11 = mux0;

    `endif

    //------------------------------------------------------------------------

    // Use concatenation operation for all signals:

    wire [11:0] all_muxes
        = { mux11 , mux10 , mux9 , mux8 ,
            mux7  , mux6  , mux5 , mux4 ,
            mux3  , mux2  , mux1 , mux0 };

    assign led = (all_muxes);

    // Use concatenation operation for the boards with 4 LEDs:

    // assign led = ({ mux3  , mux2  , mux1 , mux0 });
    // assign led = ({ mux6  , mux5  , mux4 , mux0 });
    // assign led = ({ mux9  , mux8  , mux7 , mux0 });
    // assign led = ({ mux11 , mux10 , mux4 , mux0 });
endmodule

