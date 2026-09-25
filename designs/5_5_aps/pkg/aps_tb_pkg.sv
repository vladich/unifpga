// Testbench helpers for the APS system, driving it through its pins: the
// bluster (UART programming), PS/2 and UART stimulus, and JTAG debug (reset,
// IR / DR shifts, IDCODE, control, status, breakpoints). Not in the design's
// file lists: no testbench uses them yet, and Icarus Verilog cannot compile
// them (ref ports, a parameterised class). A testbench that wants them lists
// this file under `simulation:` in fileset.yml and imports aps_tb_pkg::*.

package aps_tb_pkg;

  import bluster_pkg::*;
  import peripheral_pkg::*;
  import jtag_pkg::*;

  //============================================================
  // Bluster: programming over the UART
  //============================================================
/* -----------------------------------------------------------------------------
    Lab_15 tb tasks
*  -----------------------------------------------------------------------------
*/
  task automatic send_data(input byte mem[$], ref logic clk_i, tx_valid, tx_busy, ref logic [7:0] tx_data);
    @(posedge clk_i);
    for(int i = mem.size()-1; i >=0; i--) begin
      tx_data = mem[i];
      tx_valid = 1'b1;
      @(posedge clk_i);
      tx_valid = 1'b0;
      @(posedge clk_i);
      while(tx_busy) @(posedge clk_i);
    end
  endtask

  task automatic rcv_data(input int size, ref logic clk_i, rx_valid, tx_o, ref logic [7:0] rx_data);
    automatic logic [0:FLASH_MSG_SIZE-1][7:0] str;
    automatic logic [3:0][7:0] size_val;
    for(int i = 0; i < size; i++) begin
      @(posedge clk_i);
      while(!rx_valid)@(posedge clk_i);
      str[i] = rx_data;
      size_val[3-i] = rx_data;
    end
    case(size)
      INIT_MSG_SIZE: begin
        $display("%s", str[0:INIT_MSG_SIZE-2]);
        assert(str[0:INIT_MSG_SIZE-10] == "ready for flash starting from 0x")begin end
        else $error("Init message format is incorrect. Should be \"ready for flash starting from 0xADDR\"");
      end
      FLASH_MSG_SIZE: begin
        $display("%s", str[0:FLASH_MSG_SIZE-2]);
        assert((str[0:16] == "finished write 0x") && (str[25+:23] == " bytes starting from 0x"))begin end
        else $error("finish message format is incorrect. Should be \"finished write 0xSIZE bytes starting from 0xADDR\"");
      end
      ACK_MSG_SIZE : $display("%0d", size_val);
    endcase
    wait(tx_o);
  endtask

  task automatic program_region(input string fname, ref logic clk_i, tx_valid, rx_valid, tx_o, tx_busy, reset, ref logic [7:0] rx_data, tx_data);
    automatic int fd, start_addr;
    automatic logic [31:0] data;
    automatic byte mem[$];
    automatic byte str [4];
    automatic logic [3:0][7:0] size;
    $display("\n%0t. Start programming %s", $time, fname);
    fd = $fopen(fname, "r");
    assert(fd)
    else $fatal(1, "Can't open file %s", fname);
    void'($fscanf(fd, "@%x", start_addr));
    start_addr <<=2;
    while(!$feof(fd)) begin
      $fscanf(fd, "%x", data);
      mem.push_back(data[ 7: 0]);
      mem.push_back(data[15: 8]);
      mem.push_back(data[23:16]);
      mem.push_back(data[31:24]);
    end
    $fclose(fd);
    size = mem.size();
    str = {start_addr[7:0],start_addr[15:8],start_addr[23:16],start_addr[31:24]};
    send_data(str, clk_i, tx_valid, tx_busy, tx_data);
    rcv_data(INIT_MSG_SIZE, clk_i, rx_valid, tx_o, rx_data);
    str = {size[0],size[1],size[2],size[3]};
    send_data(str, clk_i, tx_valid, tx_busy, tx_data);
    rcv_data(ACK_MSG_SIZE, clk_i, rx_valid, tx_o, rx_data);
    send_data(mem, clk_i, tx_valid, tx_busy, tx_data);
    rcv_data(FLASH_MSG_SIZE, clk_i, rx_valid, tx_o, rx_data);
    $display("%0t. Region has been programmed", $time);
  endtask

  task automatic finish_programming(ref logic clk_i, tx_valid, tx_busy, reset, ref logic [7:0] tx_data);
    automatic byte mem[4] = {8'hff, 8'hff, 8'hff, 8'hff};
    send_data(mem, clk_i, tx_valid, tx_busy, tx_data);
    $display("Flashing is complete");
  endtask

  //============================================================
  // PS/2 and UART stimulus
  //============================================================
  task automatic ps2_send_scan_code(input logic [7:0] code, ref logic ps2_clk, ref logic ps2_dat);
    logic [11:0] data = {2'b11, !(^code), code, 1'b0};
    for(int i = 0; i < 11; i++) begin
      ps2_dat = data[i];
      #15us;
      ps2_clk = 1'b0;
      #15us;
      ps2_clk = 1'b1;
    end
  endtask

  task automatic uart_rx_send_char(input logic [7:0] character, input logic [31:0] baudrate, ref logic tx);
    logic [11:0] data = {2'b11, (^character), character, 1'b0};
    for(int i = 0; i < 12; i++) begin
      tx = data[i];
      #(1s/baudrate);
    end
  endtask

  //============================================================
  // JTAG debug
  //============================================================


  //============================================================
  // Low-level clock
  //============================================================
  task automatic jtag_clock(
    //task interface
    input logic tms_i,
    input logic tdi_i,
    output logic tdo_o,
    //dut interface
    ref logic tms_o,
    ref logic tdi_o,
    ref logic tck_o,
    ref  logic tdo_i
  );
    begin
      tck_o = 0;
      tms_o = tms_i;
      tdi_o = tdi_i;
      #100 tck_o = 1;
      tdo_o = tdo_i;
      #100 tck_o = 0;
    end
  endtask

  //============================================================
  // RESET
  //============================================================
  task automatic jtag_reset(ref logic tms_o, ref logic tdi_o, ref logic tck_o);
    logic dummy, tdo = 0;
    begin
      repeat (6) jtag_clock(1, 0, dummy, tms_o, tdi_o, tck_o, tdo);
      jtag_clock(0, 0, dummy, tms_o, tdi_o, tck_o, tdo);
    end
  endtask

  //============================================================
  // SHIFT IR
  //============================================================
  task automatic jtag_shift_ir(input logic [2:0] ir_in, ref logic tms_o, ref logic tdi_o, ref logic tck_o);
    int i;
    logic dummy, tdo = 0;

    begin
      jtag_clock(1, 0, dummy, tms_o, tdi_o, tck_o, tdo);
      jtag_clock(1, 0, dummy, tms_o, tdi_o, tck_o, tdo);

      jtag_clock(0, 0, dummy, tms_o, tdi_o, tck_o, tdo);
      jtag_clock(0, 0, dummy, tms_o, tdi_o, tck_o, tdo);

      for (i = 0; i < 3; i++) begin
        jtag_clock((i == 2), ir_in[i], dummy, tms_o, tdi_o, tck_o, tdo);
      end

      jtag_clock(1, 0, dummy, tms_o, tdi_o, tck_o, tdo);
      jtag_clock(0, 0, dummy, tms_o, tdi_o, tck_o, tdo);
    end
  endtask

  //============================================================
  // GENERIC SHIFT DR (параметризованный)
  //============================================================
  class jtag_task_wrapper #(int WIDTH = 32);
    static task automatic jtag_shift_dr(
      input  logic [WIDTH-1:0] din,
      output logic [WIDTH-1:0] dout,
      ref logic tms_o,
      ref logic tdi_o,
      ref logic tck_o,
      ref logic tdo_i
    );
      int i;
      logic dummy, tdo = 0;

      begin
        dout = '0;
        jtag_clock(1, 0, dummy, tms_o, tdi_o, tck_o, tdo);
        jtag_clock(0, 0, dummy, tms_o, tdi_o, tck_o, tdo);
        jtag_clock(0, 0, dummy, tms_o, tdi_o, tck_o, tdo);

        for (i = 0; i < WIDTH; i++) begin
          jtag_clock((i == WIDTH-1), din[i], dout[i],
                      tms_o, tdi_o, tck_o, tdo_i);
        end

        jtag_clock(1, 0, dummy, tms_o, tdi_o, tck_o, tdo);
        jtag_clock(0, 0, dummy, tms_o, tdi_o, tck_o, tdo);
      end
    endtask
  endclass

  //============================================================
  // READ IDCODE
  //============================================================
  task automatic read_idcode(output logic [31:0] idcode,
                              ref logic tms_o, ref logic tdi_o,
                              ref logic tck_o, ref logic tdo_i
  );
    logic [31:0] dout;
    logic [31:0] din;

    begin
      jtag_shift_ir(IR_IDCODE, tms_o, tdi_o, tck_o);

      din = '0;
      jtag_task_wrapper#(32)::jtag_shift_dr(din, dout, tms_o, tdi_o, tck_o, tdo_i);

      idcode = dout;
    end
  endtask

  //============================================================
  // Update CONTROL register
  //============================================================
  task automatic ctrl_req(input string field, ref logic tms_o, ref logic tdi_o, ref logic tck_o);
    control_t ctrl_in = '0;
    control_t ctrl_out;
    logic tdo = 0;

    case(field)
      "halt"      : ctrl_in.halt_request      = 1'b1;
      "resume"    : ctrl_in.resume_request    = 1'b1;
      "bp_arm"    : ctrl_in.bp_arm_request    = 1'b1;
      "bp_disarm" : ctrl_in.bp_disarm_request = 1'b1;
      "step"      : ctrl_in.step_request      = 1'b1;
    endcase
    jtag_shift_ir(IR_CONTROL, tms_o, tdi_o, tck_o);
    jtag_task_wrapper#(CONTROL_WIDTH)::jtag_shift_dr(
      ctrl_in,
      ctrl_out,
      tms_o,
      tdi_o,
      tck_o,
      tdo
    );
  endtask


  //============================================================
  // MONITOR (UNPACK через struct)
  //============================================================
  task automatic read_status(output status_t status, ref logic tms_o, ref logic tdi_o, ref logic tck_o, ref logic tdo_i);
    logic [STATUS_WIDTH-1:0] raw;
    logic [STATUS_WIDTH-1:0] din;

    begin
      jtag_shift_ir(IR_STATUS, tms_o, tdi_o, tck_o);

      din = '0;

      jtag_task_wrapper#(STATUS_WIDTH)::jtag_shift_dr(
        din,
        raw,
        tms_o,
        tdi_o,
        tck_o,
        tdo_i
      );

      status = status_t'(raw); // UNPACK
    end
  endtask

  task automatic set_breakpoint(input logic [31:0] addr, ref logic tms_o, ref logic tdi_o, ref logic tck_o, ref logic tdo_i);
    logic [31:0] dummy;

    jtag_shift_ir(IR_BREAKPOINT, tms_o, tdi_o, tck_o);
    jtag_task_wrapper#(32)::jtag_shift_dr(addr, dummy, tms_o, tdi_o, tck_o, tdo_i);
  endtask

  task automatic cpu_reset(ref logic tms_o, ref logic tdi_o, ref logic tck_o, ref logic tdo_i);
    jtag_shift_ir(IR_CPU_RST, tms_o, tdi_o, tck_o);
  endtask

  task automatic bluster_reset(ref logic tms_o, ref logic tdi_o, ref logic tck_o, ref logic tdo_i);
    jtag_shift_ir(IR_BLUSTER_RST, tms_o, tdi_o, tck_o);
  endtask

  function automatic void print_status(status_t status);
    $display("STATUS:\n  PC=%08x\n  INSTR=%08x\n  MEM_ADDR=%08x\n  MEM_DATA=%08x\n  MEM_WE=%b\n  MEM_BE=%b\n  HALTED=%b\n  BREAKPOINT=%08x\n  BP_ARMED=%b",
      status.pc, status.instr, status.mem_addr, status.mem_data, status.mem_we, status.mem_be, status.halted, status.breakpoint, status.bp_armed
    );
  endfunction

endpackage
