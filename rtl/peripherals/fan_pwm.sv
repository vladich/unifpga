// =============================================================================
// fan_pwm — one fan header as one actuator: its on/off bit is the supply
// enable (and gates the PWM), its 8-bit level the speed as a PWM duty at
// PWM_KHZ (25 kHz, what 4-pin PC fans expect). A header may have either
// output alone; the tachometer is not read yet.
// =============================================================================

module fan_pwm
# (
    parameter int CLK_MHZ = 50,
    parameter int PWM_KHZ = 25
)
(
    input        clk,
    input        rst,
    input        enable,
    input  [7:0] level,
    output       pwm,
    output       en,
    input        tach
);

    servo_pwm # (.CLK_MHZ (CLK_MHZ), .COUNT (1), .SERVO (0), .PWM_KHZ (PWM_KHZ))
        i_pwm (.clk (clk), .rst (rst), .enable (enable), .level (level), .out (pwm));

    assign en = enable;

endmodule
