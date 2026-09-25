/*******************************************************************************************/
/**                                                                                       **/
/** Copyright 2021 Monte J. Dalrymple                                                     **/
/**                                                                                       **/
/** SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1                                      **/
/**                                                                                       **/
/** Licensed under the Solderpad Hardware License v 2.1 (the "License"); you may not use  **/
/** this file except in compliance with the License, or, at your option, the Apache       **/
/** License version 2.0. You may obtain a copy of the License at                          **/
/**                                                                                       **/
/** https://solderpad.org/licenses/SHL-2.1/                                               **/
/**                                                                                       **/
/** Unless required by applicable law or agreed to in writing, any work distributed under **/
/** the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF   **/
/** ANY KIND, either express or implied. See the License for the specific language        **/
/** governing permissions and limitations under the License.                              **/
/**                                                                                       **/
/** YRV logic simulation/synthesis options                            Rev 0.0  03/29/2021 **/
/**                                                                                       **/
/*******************************************************************************************/

// One implementation for every FPGA: no vendor variants (the original's
// iCE40 / 7-series instantiated primitives are not kept). SIMULATION is defined
// by the simulation flow (./unifpga sim passes -D SIMULATION), never guessed
// from a simulator's own macros.

/*******************************************************************************************/
/* read-only csr defaults                                                                  */
/*******************************************************************************************/
`define MISA_DEF       32'h00000000                        /* misa                         */
`define MVENDORID_DEF  32'h00000000                        /* vendor id                    */
`define MARCHID_DEF    32'h00000000                        /* architecture id              */
`define MIMPID_DEF     32'h00000000                        /* implementation id            */

/*******************************************************************************************/
/* read/write csr reset values                                                             */
/*******************************************************************************************/
`define MEPC_RST   32'h00000000                            /* exception pc register        */
`define MSCR_RST   32'h00000000                            /* scratch register             */
`define MTVEC_RST  30'h00000040                            /* trap vector register  0x0100 */

/*******************************************************************************************/
/* default addresses                                                                       */
/*******************************************************************************************/
`define NMI_VECT   32'h00000100                            /* nmi vector            0x0100 */

`ifdef RESET_BASE_AND_INT_VECTORS_FOR_RARS
  `define RST_BASE   31'h00000000                          /* reset start address   0x0000 */
`else
  `define RST_BASE   31'h00000100                          /* reset start address   0x0100 */
`endif

/*******************************************************************************************/
/* debug options                                                                           */
/*******************************************************************************************/
`define XDEBUGVER   4'hf                                   /* debug version                */
`define DBG_VECT   32'h00000140                            /* debug vector          0x0140 */
`define DEX_VECT   32'h000001c0                            /* dbg exception vector  0x01c0 */

/*******************************************************************************************/
/* timer width select - default is 64-bit                                                  */
/*******************************************************************************************/
// `define TIMER_64                                           /* 64-bit                       */
// `define TIMER_32                                        /* 32-bit                       */
`define TIMER_0                                         /* none                         */

/*******************************************************************************************/
/* cycle counter width select - default is 64-bit                                          */
/*******************************************************************************************/
// `define CYCCNT_64                                          /* 64-bit                       */
// `define CYCCNT_32                                       /* 32-bit                       */
`define CYCCNT_0                                        /* none                         */

/*******************************************************************************************/
/* instructions-retired counter width select - default is 64-bit                           */
/*******************************************************************************************/
// `define INSTRET_64                                         /* 64-bit                       */
// `define INSTRET_32                                      /* 32-bit                       */
`define INSTRET_0                                       /* none                         */
