// One-byte native response register around the generated LiteX UART PHY.
// A received byte is echoed over TX. RX is inherently non-stallable; an
// arrival while the register is occupied reports the PHY's overflow pulse.
module design_top (
    input  logic       clk,
    input  logic       rst,
    input  logic       uart_rx,
    output logic       uart_tx,
    output logic       rx_framing_error,
    output logic       rx_overflow
);
    logic [7:0] response;
    logic pending;
    logic tx_ready;
    logic rx_valid;
    logic rx_ready;
    logic [7:0] rx_data;

    assign rx_ready = !pending;

    litex_rs232_phy phy (
        .sys_clk(clk), .sys_rst(rst),
        .serial_rx(uart_rx), .serial_tx(uart_tx),
        .sink_valid(pending), .sink_ready(tx_ready), .sink_data(response),
        .source_valid(rx_valid), .source_ready(rx_ready), .source_data(rx_data),
        .rx_framing_error(rx_framing_error), .rx_overflow(rx_overflow)
    );

    always_ff @(posedge clk) begin
        if (rst) begin
            pending <= 1'b0;
            response <= '0;
        end else if (rx_valid && rx_ready) begin
            response <= rx_data;
            pending <= 1'b1;
        end else if (pending && tx_ready) begin
            pending <= 1'b0;
        end
    end
endmodule
