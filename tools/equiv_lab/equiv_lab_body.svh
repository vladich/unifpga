// Shared body of the equivalence-check lab (tools/equiv_check.py). The two
// tops around it — tools/equiv_lab/bgm/lab_top.sv in BGM's lab_top dialect
// and tools/equiv_lab/unifpga/design_top.sv in unifpga's design_top dialect
// — declare eq_w_key, eq_key and eq_mic and include this file, so both sides
// compute the same function of the same inputs.
//
// Every output bit is a distinct XOR of input bits and bits of a slow
// free-running counter: a swapped, inverted, missing or misparameterised pin
// anywhere in the board top changes what the pins show, and the counter
// makes outputs move even while the inputs are held.

    // slow_clk is left out on purpose: BGM feeds it from the board top,
    // unifpga derives it inside design_top.
    //
    // Every output bit mixes one bit of each input bus, indexed modulo that
    // bus's width, so a swapped, inverted or missing pin changes what the
    // pins show while a bus that carries no pins (a dangling gpio of another
    // width) changes nothing. An input BGM leaves unconnected reads z here;
    // the vendor tools tie such an input to ground (Quartus, Vivado) or fold
    // it to a constant (yosys), so z is read as 0 on both sides and x / z
    // inputs are reported instead of poisoning every output they reach.
    localparam W_KEY  = eq_w_key > 0 ? eq_w_key : 1,
               W_SW   = w_sw     > 0 ? w_sw     : 1,
               W_GPIO = w_gpio   > 0 ? w_gpio   : 1,
               W_X    = w_x      > 0 ? w_x      : 1,
               W_Y    = w_y      > 0 ? w_y      : 1;

    // Text macros, not functions: Icarus re-evaluates a continuous assign
    // that calls a function only when the function's *arguments* change, so
    // a function reading the buses directly evaluates once, at time 0, to x.
    `define EQ_Z0(v) ((((v)) === 1'bz) ? 1'b0 : ((v)))
    `define EQ_MIX(i, a, b, c) ( `EQ_Z0(eq_key [(((i) * (a)) + 0) % W_KEY ]) \
                               ^ `EQ_Z0(sw     [(((i) * (b)) + 1) % W_SW  ]) \
                               ^ `EQ_Z0(gpio   [(((i) * (c)) + 2) % W_GPIO]) \
                               ^ `EQ_Z0(x      [(((i) * (a)) + 1) % W_X   ]) \
                               ^ `EQ_Z0(y      [(((i) * (b)) + 2) % W_Y   ]) \
                               ^ `EQ_Z0(eq_mic [(((i) * (c)) + 3) % 24    ]) \
                               ^ `EQ_Z0(uart_rx) )

    logic [31:0] cnt;
    logic [ 5:0] tick;

    always_ff @ (posedge clk or posedge rst)
        if (rst) begin
            cnt  <= '0;
            tick <= '0;
        end else begin
            tick <= tick + 1'b1;
            if (tick == '0)
                cnt <= cnt + 1'b1;
        end

    wire [w_led   - 1:0] led_w;
    wire [          7:0] abcdefgh_w;
    wire [w_digit - 1:0] digit_w;
    wire [w_red   - 1:0] red_w;
    wire [w_green - 1:0] green_w;
    wire [w_blue  - 1:0] blue_w;
    wire [         15:0] sound_w;

    genvar i;

    generate
        for (i = 0; i < w_led; i++) begin : g_led
            assign led_w [i] = `EQ_MIX(i, 3, 7, 11) ^ cnt [i % 32];
        end
        for (i = 0; i < 8; i++) begin : g_seg
            assign abcdefgh_w [i] = `EQ_MIX(i, 5, 13, 17) ^ cnt [(i + 8) % 32];
        end
        for (i = 0; i < w_digit; i++) begin : g_digit
            assign digit_w [i] = `EQ_MIX(i, 7, 19, 23) ^ cnt [(i + 16) % 32];
        end
        for (i = 0; i < w_red; i++) begin : g_red
            assign red_w [i] = `EQ_MIX(i, 11, 29, 31) ^ cnt [(i + 20) % 32];
        end
        for (i = 0; i < w_green; i++) begin : g_green
            assign green_w [i] = `EQ_MIX(i, 13, 37, 41) ^ cnt [(i + 24) % 32];
        end
        for (i = 0; i < w_blue; i++) begin : g_blue
            assign blue_w [i] = `EQ_MIX(i, 17, 43, 47) ^ cnt [(i + 28) % 32];
        end
        for (i = 0; i < 16; i++) begin : g_sound
            assign sound_w [i] = `EQ_MIX(i, 19, 53, 59) ^ `EQ_Z0(eq_mic [(i + 8) % 24]) ^ cnt [(i + 4) % 32];
        end
    endgenerate

    assign led      = led_w;
    assign abcdefgh = abcdefgh_w;
    assign digit    = digit_w;
    assign red      = red_w;
    assign green    = green_w;
    assign blue     = blue_w;
    assign sound    = sound_w;
    assign uart_tx  = `EQ_Z0(uart_rx) ^ `EQ_Z0(eq_key [0]) ^ cnt [20];

    // Symmetric probe for tools/equiv_check.py: what the lab sees at a few
    // fixed cycles (the runner keeps EQUIV-* lines from both sides).
    integer eq_cyc = 0;
    always @ (posedge clk) begin
        eq_cyc <= eq_cyc + 1;
        if (eq_cyc == 1500 || eq_cyc == 3000 || eq_cyc == 20000)
            $display ("EQUIV-PROBE cyc=%0d rst=%b cnt=%h key=%b sw=%b x=%h y=%h mic=%h uart_rx=%b gpio=%b",
                      eq_cyc, rst, cnt, eq_key, sw, x, y, eq_mic, uart_rx, gpio);
        if (eq_cyc == 3000)
            $display ("EQUIV-UNDEF key=%b sw=%b x=%b y=%b mic=%b uart_rx=%b gpio=%b  (1 = some bit x or z)",
                      (^ (eq_key ^ eq_key)) !== 1'b0, (^ (sw ^ sw)) !== 1'b0, (^ (x ^ x)) !== 1'b0,
                      (^ (y ^ y)) !== 1'b0, (^ (eq_mic ^ eq_mic)) !== 1'b0, (uart_rx ^ uart_rx) !== 1'b0,
                      (^ (gpio ^ gpio)) !== 1'b0);
    end
