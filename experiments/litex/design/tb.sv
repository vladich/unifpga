`timescale 1ns/1ps

module tb_fifo;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1;
    reg sink_valid = 0;
    wire sink_ready;
    reg [15:0] sink_data = 0;
    reg source_ready = 0;
    wire source_valid;
    wire [15:0] source_data;
    wire source_first, source_last;
    wire [2:0] level;

    litex_sync_fifo dut (
        .sys_clk(clk), .sys_rst(rst), .sink_valid(sink_valid),
        .sink_ready(sink_ready), .sink_first(1'b1), .sink_last(1'b1),
        .sink_data(sink_data), .source_valid(source_valid),
        .source_ready(source_ready), .source_first(source_first),
        .source_last(source_last), .source_data(source_data), .level(level)
    );

    initial begin
        repeat (3) @(negedge clk);
        rst = 0;
        sink_valid = 1;
        sink_data = 16'h1234;
        if (!sink_ready) $fatal(1, "empty FIFO did not accept a word");
        @(negedge clk);
        sink_data = 16'habcd;
        if (!sink_ready) $fatal(1, "FIFO did not accept a second word");
        @(negedge clk);
        sink_valid = 0;
        if (!source_valid || source_data !== 16'h1234 ||
            !source_first || !source_last || level !== 3'd2)
            $fatal(1, "generated FIFO lost a packet");
        repeat (4) begin
            @(negedge clk);
            if (!source_valid || source_data !== 16'h1234)
                $fatal(1, "generated FIFO changed a stalled packet");
        end
        source_ready = 1;
        @(negedge clk);
        if (!source_valid || source_data !== 16'habcd || level !== 3'd1)
            $fatal(1, "generated FIFO reordered packets");
        @(negedge clk);
        if (source_valid || level !== 3'd0)
            $fatal(1, "generated FIFO did not drain");
        $display("PASS standalone generated LiteX FIFO");
        $finish;
    end
endmodule

module tb;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1;
    reg pdm_data = 1;
    reg sample_ready = 0;
    wire pdm_clk, pdm_lrsel, sample_valid;
    wire [15:0] sample_data, dropped_samples;
    wire [2:0] level;
    integer accepted = 0;
    integer i;

    pdm_fifo_capture dut (
        .clk(clk), .rst(rst), .pdm_clk(pdm_clk), .pdm_data(pdm_data),
        .pdm_lrsel(pdm_lrsel), .sample_valid(sample_valid),
        .sample_ready(sample_ready), .sample_data(sample_data),
        .level(level), .dropped_samples(dropped_samples)
    );

    initial begin
        repeat (3) @(negedge clk);
        rst = 0;
        repeat (1000) @(negedge clk);
        if (level !== 3'd4 || !sample_valid || dropped_samples == 0)
            $fatal(1, "stalled microphone stream did not fill and report loss");
        if (sample_data !== 16'h2000)
            $fatal(1, "first decoded sample omitted a PDM bit");
        repeat (5) begin
            @(negedge clk);
            if (!sample_valid || sample_data !== 16'h2000)
                $fatal(1, "backpressure changed the oldest PDM sample");
        end
        sample_ready = 1;
        for (i = 0; i < 10; i = i + 1) begin
            @(posedge clk);
            if (sample_valid && sample_ready) begin
                if (sample_data !== 16'h2000 || !dut.first || !dut.last)
                    $fatal(1, "composed stream changed packet data or boundary");
                accepted = accepted + 1;
            end
        end
        if (accepted != 4)
            $fatal(1, "expected four buffered samples, got %0d", accepted);
        @(negedge clk);
        if (level !== 3'd0) $fatal(1, "capture FIFO did not drain");

        rst = 1;
        pdm_data = 0;
        sample_ready = 0;
        repeat (3) @(negedge clk);
        if (level !== 3'd0 || dropped_samples !== 16'd0)
            $fatal(1, "reset did not clear queue and loss count");
        rst = 0;
        repeat (80) @(negedge clk);
        if (!sample_valid || sample_data !== 16'he000)
            $fatal(1, "zero-bit PDM window has wrong signed result");
        $display("PASS unifpga PDM decoder + generated LiteX FIFO");
        $finish;
    end
endmodule

module tb_virtual;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1;
    reg pdm_data = 1;
    reg [0:0] btn = 0;
    wire [7:0] led;
    tri [9:0] gpio;
    integer pdm_edges = 0;
    assign gpio[0] = pdm_data;
    always @(posedge gpio[1]) pdm_edges = pdm_edges + 1;

    design_top #(.clk_mhz(24), .w_btn(1), .w_led(8), .w_gpio(10)) dut (
        .clk(clk), .rst(rst), .btn(btn), .led(led), .gpio(gpio)
    );

    initial begin
        repeat (3) @(negedge clk);
        rst = 0;
        repeat (350) @(negedge clk);
        if (pdm_edges == 0 || gpio[2] !== 1'b0 ||
            dut.capture.level !== 3'd4 || dut.capture.dropped_samples == 0)
            $fatal(1, "virtual GPIO/button adapter did not capture and account for PDM data");
        btn[0] = 1;
        repeat (10) @(negedge clk);
        if (dut.capture.level !== 3'd0)
            $fatal(1, "virtual button did not drain the FIFO");
        $display("PASS virtual-device PDM/FIFO adapter");
        $finish;
    end
endmodule

module tb_packetizer;
    reg clk = 0;
    always #50 clk = ~clk;
    reg rst = 1;
    reg sample_valid = 0;
    wire sample_ready, byte_valid;
    reg [15:0] sample_data = 0;
    reg byte_ready = 0;
    wire [7:0] byte_data;

    sample_to_uart_bytes dut (
        .clk(clk), .rst(rst),
        .sample_valid(sample_valid), .sample_ready(sample_ready),
        .sample_data(sample_data),
        .byte_valid(byte_valid), .byte_ready(byte_ready),
        .byte_data(byte_data)
    );

    initial begin
        repeat (3) @(negedge clk);
        rst = 0;
        sample_valid = 1;
        sample_data = 16'h12ab;
        @(negedge clk);
        sample_valid = 0;
        sample_data = 16'hffff;
        repeat (5) begin
            if (!byte_valid || byte_data !== 8'h12 || sample_ready)
                $fatal(1, "high byte changed while UART stalled");
            @(negedge clk);
        end
        byte_ready = 1;
        @(negedge clk);
        byte_ready = 0;
        if (!byte_valid || byte_data !== 8'hab || sample_ready)
            $fatal(1, "packetizer lost or reordered low byte");
        repeat (3) @(negedge clk);
        if (byte_data !== 8'hab) $fatal(1, "low byte changed while stalled");
        byte_ready = 1;
        @(negedge clk);
        byte_ready = 0;
        if (byte_valid || !sample_ready)
            $fatal(1, "packetizer did not release sample after both bytes");
        sample_valid = 1;
        sample_data = 16'h3456;
        @(negedge clk);
        rst = 1;
        sample_valid = 0;
        @(negedge clk);
        if (byte_valid || !sample_ready)
            $fatal(1, "reset did not discard a partial sample");
        $display("PASS native sample-to-UART packetizer backpressure and reset");
        $finish;
    end
endmodule

module tb_pdm_uart;
    localparam integer BIT_NS = 8680; // Rounded 24 MHz / 115200 baud period.
    reg clk = 0;
    always #20.833 clk = ~clk; // 24 MHz within 0.002%.
    reg rst = 1;
    reg pdm_data = 1;
    wire pdm_clk, pdm_lrsel, uart_tx;
    wire [2:0] level;
    wire [15:0] dropped_samples;
    reg [7:0] high_byte, low_byte;
    integer pdm_edges = 0;
    time first_packet_done, second_packet_done;

    always @(posedge pdm_clk) pdm_edges = pdm_edges + 1;

    pdm_uart_stream dut (
        .clk(clk), .rst(rst), .pdm_data(pdm_data),
        .pdm_clk(pdm_clk), .pdm_lrsel(pdm_lrsel),
        .uart_tx(uart_tx), .level(level), .dropped_samples(dropped_samples)
    );

    task automatic receive_byte(output reg [7:0] value);
        @(negedge uart_tx);
        #(BIT_NS / 2);
        if (uart_tx !== 1'b0) $fatal(1, "UART start bit missing");
        for (integer bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
            #(BIT_NS);
            value[bit_index] = uart_tx;
        end
        #(BIT_NS);
        if (uart_tx !== 1'b1) $fatal(1, "UART stop bit missing");
    endtask

    initial begin
        repeat (4) @(negedge clk);
        rst = 0;
        receive_byte(high_byte);
        receive_byte(low_byte);
        if ({high_byte, low_byte} !== 16'h2000)
            $fatal(1, "positive PDM sample changed over FIFO/UART: %h%h",
                   high_byte, low_byte);
        first_packet_done = $time;
        receive_byte(high_byte);
        receive_byte(low_byte);
        second_packet_done = $time;
        if ({high_byte, low_byte} !== 16'h2000)
            $fatal(1, "second PDM sample changed over FIFO/UART: %h%h",
                   high_byte, low_byte);
        if (second_packet_done - first_packet_done < 300000 ||
            second_packet_done - first_packet_done > 390000)
            $fatal(1, "unexpected serial sample cadence: %0t ns",
                   second_packet_done - first_packet_done);
        if (pdm_edges == 0 || pdm_lrsel !== 1'b0 || dropped_samples !== 16'd0)
            $fatal(1, "PDM source or matched-rate loss accounting failed");

        // Abort the next frame, clear the queue, then check a negative sample.
        rst = 1;
        pdm_data = 0;
        repeat (4) @(negedge clk);
        if (level !== 3'd0 || dropped_samples !== 16'd0)
            $fatal(1, "reset did not clear queued PDM samples and loss count");
        rst = 0;
        receive_byte(high_byte);
        receive_byte(low_byte);
        if ({high_byte, low_byte} !== 16'he000)
            $fatal(1, "negative PDM sample changed over FIFO/UART: %h%h",
                   high_byte, low_byte);
        $display("PASS PDM decoder + LiteX FIFO + native packetizer + LiteX UART");
        $finish;
    end

    initial begin
        #3000000;
        $fatal(1, "PDM/UART composed simulation timed out");
    end
endmodule
