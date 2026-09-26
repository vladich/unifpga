// APS firmware integration regression. Run through unifpga sim --tb-top
// tb_firmware. The real UART programmer releases the CPU; only the long timer
// wait is shortened by setting its counter near the firmware's threshold.
module tb_firmware;
    localparam integer UART_BIT_NS = 8680; // 115200 baud at a 1 ns timescale

    logic clk = 1'b0;
    logic rst = 1'b0;
    logic uart_rx = 1'b1;
    logic [7:0] led;

    always #10 clk = ~clk; // 50 MHz input; APS divides this to 10 MHz

    design_top #(
        .clk_mhz(50), .w_sw(8), .w_btn(4), .w_led(8), .w_digit(8),
        .w_gpio(100), .screen_width(640), .screen_height(480),
        .w_red(4), .w_green(4), .w_blue(4)
    ) dut (
        .clk(clk), .rst(rst), .sw(8'b0), .btn(4'b0),
        .led(led), .uart_rx(uart_rx)
    );

    task automatic send_uart(input logic [7:0] value);
        integer bit_index;
        begin
            uart_rx = 1'b0; // start
            #UART_BIT_NS;
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
                uart_rx = value[bit_index];
                #UART_BIT_NS;
            end
            uart_rx = ^value; // even parity, as required by Bluster
            #UART_BIT_NS;
            uart_rx = 1'b1; // stop and inter-byte idle
            #(2 * UART_BIT_NS);
        end
    endtask

    initial begin
        #1_000_000;
        $fatal(1, "APS firmware test timed out");
    end

    initial begin
        #1 rst = 1'b1;
        #100 rst = 1'b0;
        repeat (20) @(posedge dut.clk10MHz);
        if (dut.system.bl2core_rst !== 1'b1)
            $fatal(1, "programmer did not hold CPU in reset");
        if (led !== 8'b0)
            $fatal(1, "LED changed before CPU release: %h", led);

        // Bluster's four-byte all-ones finish command releases the CPU.
        send_uart(8'hff);
        send_uart(8'hff);
        send_uart(8'hff);
        send_uart(8'hff);
        wait (dut.system.bl2core_rst === 1'b0);
        repeat (100) @(posedge dut.clk10MHz);
        if (led !== 8'b0)
            $fatal(1, "LED changed before timer threshold: %h", led);

        // program.hex polls timer at 0x08000000 and stores successive bits to
        // the LED at 0x02000000 each ten million ticks. Avoid simulating a
        // full second while still exercising CPU decode, LSU and bus routing.
        @(negedge dut.clk10MHz);
        dut.system.timer_peripheral.timer_inst.system_counter = 64'd9_999_995;
        wait (led === 8'h01);
        @(negedge dut.clk10MHz);
        dut.system.timer_peripheral.timer_inst.system_counter = 64'd19_999_995;
        wait (led === 8'h02);
        $display("APS_FIRMWARE_PASS timer reads and two firmware LED writes");
        $finish;
    end
endmodule
