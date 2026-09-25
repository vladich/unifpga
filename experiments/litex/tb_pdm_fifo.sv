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

module tb_pdm_capture;
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
