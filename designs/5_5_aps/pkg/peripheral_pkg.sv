/* -----------------------------------------------------------------------------
* Project Name   : Architectures of Processor Systems (APS) lab work
* Organization   : National Research University of Electronic Technology (MIET)
* Department     : Institute of Microdevices and Control Systems
* Author(s)      : Andrei Solodovnikov
* Email(s)       : hepoh@org.miet.ru

See https://github.com/MPSU/APS/blob/master/LICENSE file for licensing details.
* ------------------------------------------------------------------------------
*/
package peripheral_pkg;

  localparam DMEM_ADDR_HIGH   = 8'h00;
  localparam SW_ADDR_HIGH     = 8'h01;
  localparam LED_ADDR_HIGH    = 8'h02;
  localparam PS2_ADDR_HIGH    = 8'h03;
  localparam HEX_ADDR_HIGH    = 8'h04;
  localparam RX_ADDR_HIGH     = 8'h05;
  localparam TX_ADDR_HIGH     = 8'h06;
  localparam VGA_ADDR_HIGH    = 8'h07;
  localparam TIMER_ADDR_HIGH  = 8'h08;

endpackage
