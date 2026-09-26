// =============================================================================
// 2_10_color_shapes_and_functions
// =============================================================================
//
// requires:
//   screen >= 320x240

module design_top
`include "design_top_interface.svh"


    logic        [         15:0] color;
    logic [(w_red + w_green + w_blue) - 1:0] rgb;

    //------------------------------------------------------------------------

    assign led        = '0;
    assign abcdefgh   = '0;
    assign digit      = '0;
    assign sound      = '0;
    assign uart_tx    = '1;
    assign rgb_r      = '0;
    assign rgb_g      = '0;
    assign rgb_b      = '0;

    //------------------------------------------------------------------------

    assign red   = rgb [((w_red + w_green + w_blue) - 1) -: w_red  ];
    assign green = rgb [((        w_green + w_blue) - 1) -: w_green];
    assign blue  = rgb [                              0  +: w_blue ];

    always_ff @ (posedge clk or posedge rst)
    begin
        if (rst)
            rgb = '0;
        else
        begin

        color [15] <= paint (x, y,  30,  30,  50,  50, 0);  // coordinates of the shapes
        color [14] <= paint (x, y, 100,  70,  50,  50, 4);  // mx, my, rx, ry, shape
        color [13] <= paint (x, y, 330,  30,  80,  80, 2);
        color [12] <= paint (x, y, 330, 330,  40,  40, 4);  //         my
        color [11] <= paint (x, y, 130, 130, 120, 120, 4);  //         |
        color [10] <= paint (x, y, 230, 230,  50,  50, 0);  //        _V_____________
        color [09] <= paint (x, y, 330, 330,  50,  50, 0);  //  mx ->|       ^       |
        color [08] <= paint (x, y, 430, 230,  50,  50, 0);  //       |       |       |
        color [07] <= paint (x, y, 100, 330, 370,  10, 0);  //       |       ry      |
        color [06] <= paint (x, y, 630, 230,  50,  50, 3);  //       |<- rx -+       |
        color [05] <= paint (x, y, 650,  30,  50,  50, 1);  //       |               |
        color [04] <= paint (x, y, 430, 130,  70,  70, 4);  //       | shape 0 square|
        color [03] <= paint (x, y, 530, 130,  50,  50, 1);  //       |_______________|
        color [02] <= paint (x, y, 130,  30,  50,  50, 1);
        color [01] <= paint (x, y,  30, 230,  80,  80, 4);  // shape 1 rhomb, 2 3^ eye
        color [00] <= paint (x, y, 530, 330,  60,  60, 4);  //     > 3 circle

        casex (color)
        16'b1???????????????: rgb <= {{w_red {1'b1}}, {w_green {1'b0}}, {w_blue {1'b0}}};
        16'b?1??????????????: rgb <= {{w_red {1'b0}}, {w_green {1'b1}}, {w_blue {1'b0}}};
        16'b??1?????????????: rgb <= {{w_red {1'b0}}, {w_green {1'b0}}, {w_blue {1'b1}}};
        16'b???1????????????: rgb <= {{w_red {1'b1}}, {w_green {1'b0}}, {w_blue {1'b1}}};
        16'b????1???????????: rgb <= {{w_red {1'b1}}, {w_green {1'b1}}, {w_blue {1'b0}}};
        16'b?????1??????????: rgb <= {{w_red {1'b1}}, {w_green {1'b0}}, {w_blue {1'b1}}};
        16'b??????1?????????: rgb <= {{w_red {1'b0}}, {w_green {1'b1}}, {w_blue {1'b1}}};
        16'b???????1????????: rgb <= {{w_red {1'b1}}, {w_green {1'b0}}, {w_blue {1'b0}}};
        16'b????????1???????: rgb <= {{w_red {1'b0}}, {w_green {1'b1}}, {w_blue {1'b0}}};
        16'b?????????1??????: rgb <= {{w_red {1'b0}}, {w_green {1'b0}}, {w_blue {1'b1}}};
        16'b??????????1?????: rgb <= {{w_red {1'b1}}, {w_green {1'b0}}, {w_blue {1'b0}}};
        16'b???????????1????: rgb <= {{w_red {1'b0}}, {w_green {1'b1}}, {w_blue {1'b0}}};
        16'b????????????1???: rgb <= {{w_red {1'b0}}, {w_green {1'b0}}, {w_blue {1'b1}}};
        16'b?????????????1??: rgb <= {{w_red {1'b1}}, {w_green {1'b0}}, {w_blue {1'b1}}};
        16'b??????????????1?: rgb <= {{w_red {1'b1}}, {w_green {1'b0}}, {w_blue {1'b0}}};
        16'b???????????????1: rgb <= {{w_red {1'b1}}, {w_green {1'b0}}, {w_blue {1'b1}}};
                     default: rgb <= '0;
        endcase

        end
    end

    //------------------------------------------------------------------------

    // draw a shape when the pixel is inside the intersection of the planes

    function automatic logic [0:0] paint (input [9:0] x, y, mx, my, rx, ry, shape);

    case (shape)
      0: paint = ((t     (x, mx, rx))       &&          (t (y, my, ry)));      // square
      1: paint = ((t     (x, mx, rx))       > (ry -     (t (y, my, ry))) &&    // rhomb
                 ((t     (x, mx, rx))       &&          (t (y, my, ry))));
      2: paint = ((s ((t (x, mx, rx)), rx)) > (ry -     (t (y, my, ry)))) &&
                  (s ((t (x, mx, rx)), rx)) &&      (s ((t (y, my, ry)), ry)); // eye
      3: paint =     ((t (x, mx, rx))       > (ry - (s ((t (y, my, ry)), ry)))) &&
                  (s ((t (x, mx, rx)), rx)) &&      (s ((t (y, my, ry)), ry)); // eye vert
    default: paint = ((s ((t (x, mx, rx)), rx)) > (ry - (s ((t (y, my, ry)), ry)))) &&
                  (s ((t (x, mx, rx)), rx)) &&      (s ((t (y, my, ry)), ry)); // circle
    endcase

    endfunction // paint

    //------------------------------------------------------------------------

    // for triangle, two planes bent at an angle of 90 degrees

    function automatic logic [9:0] t (input [9:0] z, m, r);

        if      ((z >= m)      && (z <  (m +  r)))
            t =   z  - m;
        else if ((z >= m +  r) && (z <= (m + (r << 1))))
            t =   m  - z + (r << 1);
        else
            t =  '0;

    endfunction // triangle

    //------------------------------------------------------------------------

    // for sinus from triangle, the plane bent around the sine envelope

    function automatic logic [9:0] s (input [9:0] t, r);

        if      (t < (r >> 1) - (r >> 4))            // < 0.4375  r
            s =  t + (t >> 1) - (t >> 5);            //            +  1.46875 t
        else if (t < (r >> 1) + (r >> 3))            // < 0.625   r
            s =  t - (t >> 3) + (r >> 2);            //   0.25    r + 0.875   t
        else if (t < (r >> 1) + (r >> 2) + (r >> 4)) // < 0.8125  r
            s = (t >> 1) + (t >> 5) + (r >> 1) - (r >> 5);
        else                                         //   0.46875 r + 0.53125 t
            s = (t >> 3) + (t >> 4) + r - (r >> 2);
                                                     //   0.75    r + 0.1875  t

    endfunction // sinus
endmodule

