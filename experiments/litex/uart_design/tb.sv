`timescale 1ns/1ps

module tb;
    localparam integer BIT_NS = 8680; // 10 MHz / 115200 baud, rounded.
    logic clk = 0;
    logic rst = 1;
    logic rx = 1;
    wire tx, framing_error, overflow;
    logic [7:0] echoed;
    integer framing_count = 0;
    integer overflow_count = 0;

    always #50 clk = ~clk;
    always @(posedge clk) begin
        if (framing_error) framing_count <= framing_count + 1;
        if (overflow) overflow_count <= overflow_count + 1;
    end

    design_top dut (
        .clk(clk), .rst(rst), .uart_rx(rx), .uart_tx(tx),
        .rx_framing_error(framing_error), .rx_overflow(overflow)
    );

    task automatic send_byte(input logic [7:0] value, input logic stop_bit);
        rx = 0;
        #(BIT_NS);
        for (integer bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
            rx = value[bit_index];
            #(BIT_NS);
        end
        rx = stop_bit;
        #(BIT_NS);
        rx = 1;
    endtask

    task automatic receive_byte(output logic [7:0] value);
        @(negedge tx);
        #(BIT_NS / 2);
        if (tx !== 0) $fatal(1, "UART TX start bit missing");
        for (integer bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
            #(BIT_NS);
            value[bit_index] = tx;
        end
        #(BIT_NS);
        if (tx !== 1) $fatal(1, "UART TX stop bit missing");
    endtask

    initial begin
        #300;
        rst = 0;
        fork
            send_byte(8'h53, 1'b1);
            receive_byte(echoed);
        join
        if (echoed !== 8'h53) $fatal(1, "wrong UART echo: %h", echoed);
        if (framing_count != 0 || overflow_count != 0)
            $fatal(1, "unexpected UART error on valid byte");
        #(2 * BIT_NS);
        send_byte(8'h32, 1'b0);
        #(2 * BIT_NS);
        if (framing_count != 1) $fatal(1, "invalid stop bit did not report framing error");
        if (overflow_count != 0) $fatal(1, "invalid stop bit reported overflow");
        $display("PASS native byte echo + generated LiteX UART PHY");
        $finish;
    end

    initial begin
        #1000000;
        $fatal(1, "UART echo simulation timed out");
    end
endmodule

module tb_phy;
    localparam integer BIT_NS = 8680;
    logic clk = 0;
    logic rst = 1;
    logic rx = 1;
    wire tx, sink_ready, source_valid, framing_error, overflow;
    wire [7:0] source_data;
    integer valid_count = 0;
    integer overflow_count = 0;
    logic [7:0] received;

    always #50 clk = ~clk;
    always @(posedge clk) begin
        if (source_valid) begin
            valid_count <= valid_count + 1;
            received <= source_data;
        end
        if (overflow) overflow_count <= overflow_count + 1;
    end

    litex_rs232_phy dut (
        .sys_clk(clk), .sys_rst(rst),
        .serial_rx(rx), .serial_tx(tx),
        .sink_valid(1'b0), .sink_ready(sink_ready), .sink_data(8'h00),
        .source_valid(source_valid), .source_ready(1'b0),
        .source_data(source_data),
        .rx_framing_error(framing_error), .rx_overflow(overflow)
    );

    initial begin
        #300;
        rst = 0;
        rx = 0;
        #(BIT_NS);
        for (integer bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
            rx = 8'ha6 >> bit_index & 1'b1;
            #(BIT_NS);
        end
        rx = 1;
        #(2 * BIT_NS);
        if (valid_count != 1 || received !== 8'ha6 || overflow_count != 1)
            $fatal(1, "unstallable RX contract failed: count=%0d data=%h overflow=%0d",
                   valid_count, received, overflow_count);
        $display("PASS generated LiteX UART RX overflow contract");
        $finish;
    end

    initial begin
        #300000;
        $fatal(1, "UART PHY simulation timed out");
    end
endmodule
