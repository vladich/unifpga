// Register writes that set up a WM8731 / SSM2603 audio codec (I2C address
// 0x34) as an I2S DAC; `include`d inside the modules that program one
// (wm8731_i2s_out, hdmi_adv7513 on the C5GX). Each word is {7-bit register,
// 9-bit value} after the device address; the table is i2c_reg_writer's TABLE.

localparam int WM8731_INIT_N = 10;

localparam [WM8731_INIT_N * 24 - 1:0] WM8731_INIT_TABLE =
{
    24'h34_0017,    // R0  left line in: 0 dB, unmuted
    24'h34_0217,    // R1  right line in: 0 dB, unmuted
    24'h34_0460,    // R2  left headphone volume
    24'h34_0660,    // R3  right headphone volume
    24'h34_08D2,    // R4  analogue path: DAC selected, microphone muted
    24'h34_0A06,    // R5  digital path: 48 kHz de-emphasis, DAC unmuted
    24'h34_0C00,    // R6  power: everything on
    24'h34_0E02,    // R7  interface: I2S, 16 bit, codec is the clock slave
    24'h34_1002,    // R8  sampling: normal mode, 48 kHz
    24'h34_1201     // R9  active
};
