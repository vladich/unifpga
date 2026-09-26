// Included inside a testbench with UART_BIT_NS and logic uart_tx.
task automatic receive_uart(output logic [7:0] value);
    integer bit_index;
    begin
        @(negedge uart_tx);
        #(UART_BIT_NS / 2);
        if (uart_tx !== 1'b0) $fatal(1, "UART start bit invalid");
        for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
            #UART_BIT_NS;
            value[bit_index] = uart_tx;
        end
        #UART_BIT_NS;
        if (uart_tx !== 1'b1) $fatal(1, "UART stop bit invalid");
    end
endtask
