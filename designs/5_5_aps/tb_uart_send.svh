// Included inside APS testbench modules that declare logic uart_rx.
localparam integer UART_BIT_NS = 8680; // 115200 baud at a 1 ns timescale

task automatic send_programmer_byte(input logic [7:0] value);
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
