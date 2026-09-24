// =============================================================================
// 3_4_music_player
// =============================================================================
//

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
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
    // assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    logic  [2:0] octave;
    logic  [3:0] note;

    //------------------------------------------------------------------------

    /*
     * TODO: Exercise 1:
     * Decrease sound level.
     */
    tone_sel
    # (
        .clk_mhz (clk_mhz)
    )
    wave_gen
    (
        .clk       ( clk       ),
        .reset     ( rst       ),
        .octave    ( octave    ),
        .note      ( note      ),
        .y         ( sound     )
    );

    //------------------------------------------------------------------------

    /*
     * TODO: Exercise 2:
     * Change (increase/decrease) music speed.
     */
    logic [23:0] clk_div;

    always @ (posedge clk or posedge rst)
        if (rst)
            clk_div <= 0;
        else
            clk_div <= clk_div + 1;

    logic  [5:0] note_cnt;

    always @ (posedge clk or posedge rst)
        if (rst)
            note_cnt <= 0;
        else
            if (&clk_div && note != silence)
                note_cnt <= note_cnt + 1;

    //------------------------------------------------------------------------

    localparam [3:0] C  = 4'd0,
                     Cs = 4'd1,
                     D  = 4'd2,
                     Ds = 4'd3,
                     E  = 4'd4,
                     F  = 4'd5,
                     Fs = 4'd6,
                     G  = 4'd7,
                     Gs = 4'd8,
                     A  = 4'd9,
                     As = 4'd10,
                     B  = 4'd11;

    localparam [3:0] Df = Cs, Ef = Ds, Gf = Fs, Af = Gs, Bf = As;

    localparam [3:0] silence = 4'd12;

    /*
     * TODO: Exercise 3:
     * Add another soundtrack.
     */

    always_comb
        case (note_cnt)
        0:  { octave, note } = { 3'b1, E  };
        1:  { octave, note } = { 3'b1, Ds };
        2:  { octave, note } = { 3'b1, E  };
        3:  { octave, note } = { 3'b1, Ds };
        4:  { octave, note } = { 3'b1, E  };

        5:  { octave, note } = { 3'b0, B  };
        6:  { octave, note } = { 3'b1, D  };
        7:  { octave, note } = { 3'b1, C  };
        8:  { octave, note } = { 3'b0, A  };
        9:  { octave, note } = { 3'b0, A  };

        10: { octave, note } = { 3'b0, C  };
        11: { octave, note } = { 3'b0, E  };
        12: { octave, note } = { 3'b0, A  };
        13: { octave, note } = { 3'b0, B  };
        14: { octave, note } = { 3'b0, B  };

        15: { octave, note } = { 3'b0, E  };
        16: { octave, note } = { 3'b0, Gs };
        17: { octave, note } = { 3'b0, B  };
        18: { octave, note } = { 3'b1, C  };
        19: { octave, note } = { 3'b1, C  };

        20: { octave, note } = { 3'b1, E  };
        21: { octave, note } = { 3'b1, Ds };
        22: { octave, note } = { 3'b1, E  };
        23: { octave, note } = { 3'b1, Ds };
        24: { octave, note } = { 3'b1, E  };

        25: { octave, note } = { 3'b0, B  };
        26: { octave, note } = { 3'b1, D  };
        27: { octave, note } = { 3'b1, C  };
        28: { octave, note } = { 3'b0, A  };
        29: { octave, note } = { 3'b0, A  };

        30: { octave, note } = { 3'b0, C  };
        31: { octave, note } = { 3'b0, E  };
        32: { octave, note } = { 3'b0, A  };
        33: { octave, note } = { 3'b0, B  };
        34: { octave, note } = { 3'b0, B  };

        35: { octave, note } = { 3'b0, E  };
        36: { octave, note } = { 3'b1, C  };
        37: { octave, note } = { 3'b0, B  };
        38: { octave, note } = { 3'b0, A  };
        39: { octave, note } = { 3'b0, A  };

        40: { octave, note } = { 3'b0, B  };
        41: { octave, note } = { 3'b1, C  };
        42: { octave, note } = { 3'b1, D  };
        43: { octave, note } = { 3'b1, E  };
        44: { octave, note } = { 3'b1, E  };
        45: { octave, note } = { 3'b1, E  };

        46: { octave, note } = { 3'b0, G  };
        47: { octave, note } = { 3'b1, F  };
        48: { octave, note } = { 3'b1, E  };
        49: { octave, note } = { 3'b1, D  };
        50: { octave, note } = { 3'b1, D  };
        51: { octave, note } = { 3'b1, D  };

        52: { octave, note } = { 3'b0, F  };
        53: { octave, note } = { 3'b1, E  };
        54: { octave, note } = { 3'b1, D  };
        55: { octave, note } = { 3'b1, C  };
        56: { octave, note } = { 3'b1, C  };
        57: { octave, note } = { 3'b1, C  };

        58: { octave, note } = { 3'b0, E  };
        59: { octave, note } = { 3'b1, D  };
        60: { octave, note } = { 3'b1, C  };
        61: { octave, note } = { 3'b0, B  };

        default: { octave, note } = { 3'b0, silence };
        endcase

//    always_comb
//        case (note_cnt)
//        0:  { octave, note } = { 3'b0, G   };
//        1:  { octave, note } = { 3'b1, C   };
//        2:  { octave, note } = { 3'b1, Ef  };
//
//        3:  { octave, note } = { 3'b1, D   };
//        4:  { octave, note } = { 3'b1, C   };
//        5:  { octave, note } = { 3'b1, Ef  };
//        6:  { octave, note } = { 3'b1, C   };
//        7:  { octave, note } = { 3'b1, D   };
//        8:  { octave, note } = { 3'b1, C   };
//        9:  { octave, note } = { 3'b0, Af  };
//        10: { octave, note } = { 3'b0, Bf  };
//
//        11: { octave, note } = { 3'b0, G   };
//        12: { octave, note } = { 3'b0, G   };
//        13: { octave, note } = { 3'b0, G   };
//        13: { octave, note } = { 3'b0, G   };
//
//        14: { octave, note } = { 3'b1, C   };
//        15: { octave, note } = { 3'b1, Ef  };
//        16: { octave, note } = { 3'b1, D   };
//        17: { octave, note } = { 3'b1, C   };
//        18: { octave, note } = { 3'b1, Ef  };
//        19: { octave, note } = { 3'b1, C   };
//        20: { octave, note } = { 3'b1, D   };
//
//        21: { octave, note } = { 3'b1, C   };
//        22: { octave, note } = { 3'b0, G   };
//        23: { octave, note } = { 3'b0, Gf  };
//        24: { octave, note } = { 3'b0, F   };
//        25: { octave, note } = { 3'b0, F   };
//        26: { octave, note } = { 3'b0, F   };
//        default: { octave, note } = { 3'b0, silence };
//        endcase

    //------------------------------------------------------------------------

    assign led  = { {(w_led - $left (octave)){1'b0}}, octave };

    assign digit = { {(w_digit - 1){1'b0}}, 1'b1};

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            abcdefgh <= 'b00000000;
        else
            case (note)
            'd0:    abcdefgh <= 'b10011100;  // C   // abcdefgh
            'd1:    abcdefgh <= 'b10011101;  // C#
            'd2:    abcdefgh <= 'b01111010;  // D   //   --a--
            'd3:    abcdefgh <= 'b01111011;  // D#  //  |     |
            'd4:    abcdefgh <= 'b10011110;  // E   //  f     b
            'd5:    abcdefgh <= 'b10001110;  // F   //  |     |
            'd6:    abcdefgh <= 'b10001111;  // F#  //   --g--
            'd7:    abcdefgh <= 'b10111100;  // G   //  |     |
            'd8:    abcdefgh <= 'b10111101;  // G#  //  e     c
            'd9:    abcdefgh <= 'b11101110;  // A   //  |     |
            'd10:   abcdefgh <= 'b11101111;  // A#  //   --d--  h
            'd11:   abcdefgh <= 'b00111110;  // B
            default: abcdefgh <= 'b00000000;
            endcase
endmodule

