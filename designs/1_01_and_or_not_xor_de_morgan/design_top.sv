// =============================================================================
// 1_01_and_or_not_xor_de_morgan
// =============================================================================
//
// requires:
//   leds >= 2
//   buttons >= 2

module design_top
`include "design_top_interface.svh"


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

    wire a = btn [0];
    wire b = btn [1];

    wire result = a ^ b;

    assign led [0] = result;

    assign led [1] = btn [0] ^ btn [1];
    // assign led [2] = a ^ b;

    //------------------------------------------------------------------------

    /*

    generate
        if (w_led > 2)
        begin : unused_led
            assign led [w_led - 1:2] = '0;
        end
    endgenerate

    */

    //------------------------------------------------------------------------

    // Exercise 1: Change the code below.
    // Assign to led [2] the result of AND operation.
    //
    // If led [2] is not available on your board,
    // comment out the code above and reuse led [0].

    // assign led [2] =

    // Exercise 2: Change the code below.
    // Assign to led [3] the result of XOR operation
    // without using "^" operation.
    // Use only operations "&", "|", "~" and parenthesis, "(" and ")".

    // assign led [3] =

    // Exercise 3: Create an illustration to De Morgan's laws:
    //
    // ~ (a & b) == ~ a | ~ b
    // ~ (a | b) == ~ a & ~ b
endmodule

