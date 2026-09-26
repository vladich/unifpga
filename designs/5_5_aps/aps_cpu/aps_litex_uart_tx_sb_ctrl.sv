// APS slot 6 TX adapter for the pinned LiteX RS232PHY export. The slot uses
// a fixed 10 MHz / 115200 baud 8N1 contract in this variant. Only data writes
// at offset 0 and ready reads at offset 8 are supported; unsupported offsets
// leave ready low, so firmware cannot silently treat the native UART's baud,
// parity or stop-bit registers as configurable.
module aps_litex_uart_tx_sb_ctrl (
  input  logic        clk_i,
  input  logic        rst_i,
  input  logic        req_i,
  input  logic        write_enable_i,
  input  logic [31:0] addr_i,
  input  logic [31:0] write_data_i,
  output logic [31:0] read_data_o,
  output logic        ready_o,
  output logic        tx_o
);
  typedef enum logic [1:0] {IDLE, WAIT_TX, ACK} state_t;
  state_t state;
  logic [7:0] tx_data;
  logic tx_ready;
  logic data_write;
  logic abandoned;

  assign data_write = req_i && write_enable_i && addr_i == 32'h0;
  assign ready_o = !req_i ? 1'b1 :
                   data_write ? (state == ACK && !abandoned) :
                   (!write_enable_i && addr_i == 32'h8);
  assign read_data_o = {31'b0, state == IDLE};

  litex_rs232_phy phy (
    .sys_clk(clk_i), .sys_rst(rst_i),
    .serial_rx(1'b1), .serial_tx(tx_o),
    .sink_valid(state == WAIT_TX), .sink_ready(tx_ready), .sink_data(tx_data),
    .source_valid(), .source_ready(1'b1), .source_data(),
    .rx_framing_error(), .rx_overflow()
  );

  always_ff @(posedge clk_i) begin
    if (rst_i) begin
      state <= IDLE;
      abandoned <= 1'b0;
      tx_data <= '0;
    end else begin
      case (state)
        IDLE: if (data_write) begin
          tx_data <= write_data_i[7:0];
          abandoned <= 1'b0;
          state <= WAIT_TX;
        end
        // Once LiteX sees valid, the serial side effect cannot be cancelled
        // by withdrawing the bus request. Retire it before taking a new one.
        WAIT_TX: begin
          if (!data_write) abandoned <= 1'b1;
          if (tx_ready) state <= ACK;
        end
        ACK: state <= IDLE;
        default: state <= IDLE;
      endcase
    end
  end
endmodule
