// =============================================================================
// 1_04_priority_encoder
// =============================================================================
//
// requires:
//   buttons >= 1 if !(w_btn >= 3) && !(w_sw >= 3) && !(w_btn + w_sw >= 3) && !(w_btn >= 2)

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

    wire [2:0] in;

    generate

        if (w_btn >= 3)              // Board with at least 3 keys
            assign in = btn [2:0];
        else if (w_sw >= 3)          // Board with at least 3 switches
            assign in = sw  [2:0];
        else if (w_btn + w_sw >= 3)  // Board with at least 3 keys + switches
            assign in = 3' ({ btn, sw });
        else if (w_btn >= 2)         // Board with at least 2 keys
            assign in = { btn [0], btn [1:0] };
        else                         // Corner case: repeat a btn 3 times
            assign in = { 3 { btn [0] } };

    endgenerate

    //------------------------------------------------------------------------

    logic [1:0] enc0, enc1, enc2, enc3;

    // Implementation 1. Priority encoder using a chain of "ifs"

    always_comb
             if (in [0]) enc0 = 2'd0;
        else if (in [1]) enc0 = 2'd1;
        else if (in [2]) enc0 = 2'd2;
        else             enc0 = 2'd0;

    // Implementation 2. Priority encoder using casez

    always_comb
        casez (in)
        3'b??1:  enc1 = 2'd0;
        3'b?10:  enc1 = 2'd1;
        3'b100:  enc1 = 2'd2;
        default: enc1 = 2'd0;
        endcase

    // Implementation 3: Combination of priority arbiter
    // and encoder without priority

    localparam w = 3;

    `ifdef YOSYS

        wire [w - 1:0] c;

        genvar i;

        generate
            for (i = 0; i < w; i = i + 1) begin
                if (i == 0)
                    assign c [0] = 1'b1;
                else
                    assign c [i] = ~ in [i - 1] & c [i - 1];
            end
        endgenerate

    `else

        wire [w - 1:0] c = { ~ in [w - 2:0] & c [w - 2:0], 1'b1 };

    `endif

    wire [w - 1:0] g = in & c;


    always_comb
        unique case (g)
        3'b001:  enc2 = 2'd0;
        3'b010:  enc2 = 2'd1;
        3'b100:  enc2 = 2'd2;
        default: enc2 = 2'd0;
        endcase

    /*
    // A variation of Implementation 3: Using unusual case of "case"

    always_comb
        unique case (1'b1)
        g [0]:   enc2 = 2'd0;
        g [1]:   enc2 = 2'd1;
        g [2]:   enc2 = 2'd2;
        default: enc2 = 2'd0;
        endcase
    */

    // A note on obsolete practice:
    //
    // Before the SystemVerilog construct "unique case"
    // got supported by the synthesis tools,
    // the designers were using pseudo-comment "synopsys parallel_case":
    //
    // SystemVerilog : unique case (1'b1)
    // Verilog 2001  : case (1'b1)  // synopsys parallel_case

    // Implementation 4: Using "for" loop

    always_comb
    begin
        enc3 = '0;

        for (int i = 0; i < $bits (in); i ++)
        begin
            if (in [i])
            begin
                enc3 = 2' (i);

                // Since both Icarus and Yosys do not support break statement
                // we simply imitate it by setting i to final value

                `ifdef __ICARUS__
                     i = $bits (in);
                `else
                    `ifdef YOSYS
                        i = $bits (in);
                    `else
                        break;
                    `endif
                `endif
            end
        end
    end

    //------------------------------------------------------------------------

    assign led = ({ enc0, enc1, enc2, enc3 });
endmodule

