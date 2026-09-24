// =============================================================================
// 6_1_geiger_muller_radiation_counter_on_tang_nano_9k
// =============================================================================
//
// requires:
//   seven_segment >= 1
//   gpio >= 8

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
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    // This is board-specific

    localparam shield_gpio_base = 3, // 3 is for Tang Nano 9K, but 36 is for Terasic DE10-Lite
               n_bulbs          = 2,
               w_bulbs          = $clog2 (n_bulbs + 1);

    wire [1:0] geiger_input_raw = gpio [shield_gpio_base + 2 +: n_bulbs];
    wire [1:0] shield_led;
    wire       shield_speaker;
    wire       shield_pwm;

    // Not applicable for Terasic DE10-Lite
    // assign gpio [shield_gpio_base + 8 +: 2] = shield_led;
    // assign gpio [shield_gpio_base + 4 +: 2] = { shield_pwm, shield_speaker };

    // assign shield_led     = geiger_input;
    // assign shield_speaker = | geiger_input;
    // assign shield_pwm     = '0;

    //------------------------------------------------------------------------

    // Sync the signal

    logic [n_bulbs - 1:0] geiger_input_sync1, geiger_input;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            geiger_input_sync1 <= '0;
            geiger_input       <= '0;
        end
        else
        begin
            geiger_input_sync1 <= geiger_input_raw;
            geiger_input       <= geiger_input_sync1;
        end

    //------------------------------------------------------------------------

    // Finding the edge

    logic [n_bulbs - 1:0] geiger_input_r;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            geiger_input_r <= '0;
        else
            geiger_input_r <= geiger_input;

    wire [n_bulbs - 1:0] geiger_posedge = geiger_input & ~ geiger_input_r;

    //------------------------------------------------------------------------

    logic [w_bulbs - 1:0] num_geiger_input;

    always_comb
    begin
        num_geiger_input = '0;

        for (int i = 0; i < n_bulbs; i ++)
            num_geiger_input += geiger_input [i];
    end

    //------------------------------------------------------------------------

    logic [w_bulbs - 1:0] num_geiger_posedge;

    always_comb
    begin
        num_geiger_posedge = '0;

        for (int i = 0; i < n_bulbs; i ++)
            num_geiger_posedge += geiger_posedge [i];
    end

    //------------------------------------------------------------------------

    localparam w_particle_cnt = 6 * 4;
    logic [w_particle_cnt - 1:0] particle_cnt;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            particle_cnt <= '0;
        else
            particle_cnt <= particle_cnt + num_geiger_posedge;

    //------------------------------------------------------------------------

    localparam w_particle_time_cnt = w_digit * 4 - w_particle_cnt;
    logic [w_particle_time_cnt - 1:0] particle_time_cnt;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            particle_time_cnt <= '0;
        else
            particle_time_cnt <= particle_time_cnt + num_geiger_input;

    //------------------------------------------------------------------------

    localparam pulse_extension = clk_mhz / 5 * 1000 * 1000,
               w_ext_cnt       = $clog2 (pulse_extension);

    logic [n_bulbs - 1:0] geiger_input_ext;

    generate

        genvar i;

        for (i = 0; i < n_bulbs; i ++)
        begin : gen

            logic [w_ext_cnt - 1:0] ext_cnt;

            always_ff @ (posedge clk or posedge rst)
                if (rst)
                    ext_cnt <= '0;
                else if (geiger_posedge [i])
                    ext_cnt <= (pulse_extension);
                else if (ext_cnt != '0)
                    ext_cnt <= ext_cnt - 1'd1;

            assign geiger_input_ext [i] = | ext_cnt;
        end

    endgenerate

    //------------------------------------------------------------------------

    assign led = ({ w_led / n_bulbs { geiger_input_ext } });

    //------------------------------------------------------------------------

    // 4 bits per hexadecimal digit
    localparam w_display_number = w_digit * 4;

    seven_segment_display # (w_digit) i_7segment
    (
        .clk      ( clk                                                     ),
        .rst      ( rst                                                     ),
        .number   ( ({ particle_time_cnt, particle_cnt } )),
        .dots     ( ({ w_digit / n_bulbs { geiger_input_ext } }   )),
        .abcdefgh ( abcdefgh                                                ),
        .digit    ( digit                                                   )
    );

    //------------------------------------------------------------------------
endmodule

