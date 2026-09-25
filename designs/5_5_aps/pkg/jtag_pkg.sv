package jtag_pkg;
  localparam logic [31:0] IDCODE = 32'hA5A5_A5A5;
  //============================================================
  // IR значения (как в RTL)
  //============================================================
  typedef enum logic [2:0] {
    IR_BYPASS     = 3'h7,
    IR_IDCODE     = 3'h1,
    IR_CONTROL    = 3'h2,
    IR_STATUS     = 3'h3,
    IR_BREAKPOINT = 3'h4,
    IR_CPU_RST    = 3'h5,
    IR_BLUSTER_RST= 3'h6
  } ir_t;

  //============================================================
  // STATUS struct (ДОЛЖЕН совпадать с RTL!)
  //============================================================
  typedef struct packed {
    logic [31:0] pc;
    logic [31:0] instr;
    logic [31:0] mem_addr;
    logic [31:0] mem_data;
    logic        mem_we;
    logic [3:0]  mem_be;
    logic        halted;
    logic [31:0] breakpoint;
    logic        bp_armed;
    logic        padding;
  } status_t;


  //============================================================
  // CONTROL struct
  //============================================================
  typedef struct packed {
    logic halt_request;
    logic resume_request;
    logic bp_arm_request;
    logic bp_disarm_request;
    logic step_request;
  } control_t;


endpackage
