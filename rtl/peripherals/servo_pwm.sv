// Pulse outputs for actuators: RC servos or PWM (motor drivers, heaters,
// dimmers). Output i takes actuator i's enable bit enable[i] and its 8-bit
// level level[8*i +: 8]:
//
//   SERVO = 1: a 20 ms frame, a pulse of 1 ms + level/255 ms (1 .. 2 ms) while
//              enable[i], nothing while off (the servo holds no position);
//   SERVO = 0: PWM at PWM_KHZ, high level/256 of the period while enable[i].

module servo_pwm
# (
    parameter int CLK_MHZ = 50,
    parameter int COUNT   = 1,
    parameter bit SERVO   = 1,
    parameter int PWM_KHZ = 20
)
(
    input                        clk,
    input                        rst,
    input        [COUNT     - 1:0] enable,
    input        [COUNT * 8 - 1:0] level,
    output logic [COUNT     - 1:0] out
);

    // one tick: 1 us for servos, 1/256 of the PWM period otherwise
    localparam int TICK_RAW = SERVO ? CLK_MHZ : (CLK_MHZ * 1000) / (PWM_KHZ * 256);
    localparam int TICK     = TICK_RAW > 0 ? TICK_RAW : 1;
    localparam int FRAME    = SERVO ? 20000 : 256;

    logic [$clog2(TICK + 1)  - 1:0] cyc;
    logic [$clog2(FRAME + 1) - 1:0] t;

    always_ff @ (posedge clk)
        if (rst)
        begin
            cyc <= '0;
            t   <= '0;
        end
        else if (cyc != TICK - 1)
            cyc <= cyc + 1'd1;
        else
        begin
            cyc <= '0;
            t   <= t == FRAME - 1 ? '0 : t + 1'd1;
        end

    for (genvar i = 0; i < COUNT; i++)
    begin : g_out
        wire [7:0]  lv    = level [8 * i +: 8];
        // 1000 us + level * 1000 / 255, as level * 1004 / 256 (within 1 us)
        wire [15:0] pulse = 16'd1000 + 16'((18'(lv) * 18'd1004) >> 8);

        always_ff @ (posedge clk)
            if (rst)
                out [i] <= 1'b0;
            else if (SERVO)
                out [i] <= enable [i] && 16'(t) < pulse;
            else
                out [i] <= enable [i] && 9'(t) < 9'(lv);
    end

endmodule
