// Firmware-visible APS timer and LED plus a generated LiteX UART TX PHY.
// Run with the pinned rs232-phy export through unifpga sim --component-export.
module tb_litex_uart;
    logic clk = 1'b0;
    logic rst = 1'b0;
    logic uart_rx = 1'b1;
    logic uart_tx;
    logic [15:0] led;

    always #50 clk = ~clk; // direct 10 MHz APS system clock

    processor_system #(.USE_LITEX_UART_TX(1'b1)) dut (
        .clk10mhz_i(clk), .clk25175khz_i(clk), .rst_i(rst),
        .sw_i(16'b0), .led_o(led),
        .kclk_i(1'b1), .kdata_i(1'b1),
        .hex_led_o(), .hex_sel_o(),
        .rx_i(uart_rx), .tx_o(uart_tx),
        .vga_r_o(), .vga_g_o(), .vga_b_o(), .vga_hs_o(), .vga_vs_o(),
        .tck_i(1'b0), .tms_i(1'b0), .tdi_i(1'b0), .tdo_o(), .tdo_en_o()
    );

`include "tb_uart_send.svh"
`include "tb_uart_receive.svh"

    initial begin
        #2_000_000;
        $fatal(1, "APS-LiteX UART firmware test timed out");
    end

    initial begin : exercise
        logic [7:0] first_byte, second_byte;
        #1;
        $readmemh("program_litex_uart.hex", dut.imem_inst.ROM, 0, 12);
        rst = 1'b1;
        #100 rst = 1'b0;
        repeat (20) @(posedge clk);
        if (dut.bl2core_rst !== 1'b1)
            $fatal(1, "programmer did not hold CPU in reset");

        send_programmer_byte(8'hff);
        send_programmer_byte(8'hff);
        send_programmer_byte(8'hff);
        fork
            send_programmer_byte(8'hff);
            begin
                wait (dut.bl2core_rst === 1'b0);
                wait (led === 16'h0001);
                receive_uart(first_byte);
                receive_uart(second_byte);
            end
        join
        if (first_byte !== 8'h55 || second_byte !== 8'ha5)
            $fatal(1, "firmware UART bytes mismatch: %h %h", first_byte, second_byte);
        $display("APS_LITEX_UART_PASS timer LED and two ordered UART bytes");
        $finish;
    end
endmodule

// Exercise unsupported register access and a request withdrawn after LiteX
// starts transmitting. A second request must still serialize correctly.
module tb_litex_uart_bridge;
    localparam integer UART_BIT_NS = 8680;
    logic clk = 1'b0;
    logic rst = 1'b1;
    logic req = 1'b0;
    logic write_enable = 1'b0;
    logic [31:0] addr = '0;
    logic [31:0] write_data = '0;
    logic [31:0] read_data;
    logic ready;
    logic uart_tx;

    always #50 clk = ~clk;

    aps_litex_uart_tx_sb_ctrl dut (
        .clk_i(clk), .rst_i(rst), .req_i(req), .write_enable_i(write_enable),
        .addr_i(addr), .write_data_i(write_data), .read_data_o(read_data),
        .ready_o(ready), .tx_o(uart_tx)
    );

`include "tb_uart_receive.svh"

    initial begin
        #400_000;
        $fatal(1, "APS-LiteX UART adapter test timed out");
    end

    initial begin : exercise_adapter
        logic [7:0] first_byte, second_byte;
        #100 rst = 1'b0;
        @(negedge clk);
        req = 1'b1;
        write_enable = 1'b1;
        addr = 32'h0c; // native variable-baud register is unsupported
        repeat (3) @(posedge clk);
        if (ready !== 1'b0 || uart_tx !== 1'b1)
            $fatal(1, "unsupported UART register write was acknowledged");
        @(negedge clk) req = 1'b0;
        @(negedge clk);
        addr = 32'h8;
        write_enable = 1'b0;
        req = 1'b1;
        @(negedge clk);
        if (ready !== 1'b1 || read_data !== 32'h1)
            $fatal(1, "idle UART status read failed: %h", read_data);
        req = 1'b0;

        fork
            begin
                receive_uart(first_byte);
                receive_uart(second_byte);
            end
            begin
                @(negedge clk);
                addr = 32'h0;
                write_data = 32'hc3;
                write_enable = 1'b1;
                req = 1'b1;
                @(negedge clk) req = 1'b0; // request withdrawn mid-transmission
                @(negedge clk);
                write_enable = 1'b0;
                addr = 32'h8;
                req = 1'b1;
                @(negedge clk);
                if (ready !== 1'b1 || read_data !== 32'h0)
                    $fatal(1, "busy UART status read failed: %h", read_data);
                req = 1'b0;
                @(negedge clk);
                write_enable = 1'b1;
                addr = 32'h0;
                write_data = 32'h5a;
                req = 1'b1;
                @(posedge clk);
                wait (ready === 1'b1);
                @(negedge clk) req = 1'b0;
            end
        join

        if (first_byte !== 8'hc3 || second_byte !== 8'h5a)
            $fatal(1, "adapter bytes mismatch after withdrawal: %h %h",
                   first_byte, second_byte);
        $display("APS_LITEX_UART_BRIDGE_PASS unsupported offset and request withdrawal");
        $finish;
    end
endmodule
