// Asynchronous reset here is needed for some FPGA boards we use


module vga
# (
    parameter N_MIXER_PIPE_STAGES = 0,

              HPOS_WIDTH          = 10,
              VPOS_WIDTH          = 10,

              // Horizontal constants

              H_DISPLAY           = 640,  // Horizontal display width
              H_FRONT             =  16,  // Horizontal right border (front porch)
              H_SYNC              =  96,  // Horizontal sync width
              H_BACK              =  48,  // Horizontal left border (back porch)

              // Vertical constants

              V_DISPLAY           = 480,  // Vertical display height
              V_BOTTOM            =  10,  // Vertical bottom border
              V_SYNC              =   2,  // Vertical sync # lines
              V_TOP               =  33,  // Vertical top border

              CLK_MHZ             =  50,  // Clock frequency (50 or 100 MHz)
              PIXEL_MHZ           =  25,  // Pixel clock frequency of VGA in MHz

              // Colour path: the user's channel widths (what design_top sees)
              // and the board's pin widths. Mirrors what BGM's
              // board_specific_top.sv does inline:
              //   same width      -> vga_r = display_on ? red : '0
              //   one pin         -> vga_r = display_on & (| red)   (omdazz, zeowaa, piswords6)
              //   fewer pins      -> the MSBs, gated by display_on

              W_RED               =   4,
              W_GREEN             =   4,
              W_BLUE              =   4,
              W_RED_O             =   4,
              W_GREEN_O           =   4,
              W_BLUE_O            =   4,

              // How the colours reach the pins (BGM's boards differ):
              //   GATE = 1          vga_r = display_on ? red : '0  (basys3, nexys4, omdazz)
              //   GATE = 0          vga_r = red — a DAC with BLANK_N (de1_soc,
              //                     sockit, de2_115) or an ungated header (emooc)
              //   REGISTERED = 1    colours and syncs through a flop, async
              //                     reset (de0_nano: "to remove the glitches")
              GATE                =   1,
              REGISTERED          =   0
)
(
    input                           clk,
    input                           rst,
    output logic                    hsync,
    output logic                    vsync,
    output logic                    display_on,
    output logic [HPOS_WIDTH - 1:0] hpos,
    output logic [VPOS_WIDTH - 1:0] vpos,
    output logic                    pixel_clk,

    input        [W_RED     - 1:0]  red,
    input        [W_GREEN   - 1:0]  green,
    input        [W_BLUE    - 1:0]  blue,
    output logic [W_RED_O   - 1:0]  vga_r,
    output logic [W_GREEN_O - 1:0]  vga_g,
    output logic [W_BLUE_O  - 1:0]  vga_b
);

    // Colour outputs gated by display_on (blanking), reduced to the pin width:
    // same width -> as is; one pin -> OR of the channel; fewer pins -> MSBs.

    logic [W_RED_O   - 1:0] red_o;
    logic [W_GREEN_O - 1:0] green_o;
    logic [W_BLUE_O  - 1:0] blue_o;

    generate
        if (W_RED_O == W_RED)     assign red_o = red;
        else if (W_RED_O == 1)    assign red_o = | red;
        else                      assign red_o = red [W_RED - 1 -: W_RED_O];

        if (W_GREEN_O == W_GREEN) assign green_o = green;
        else if (W_GREEN_O == 1)  assign green_o = | green;
        else                      assign green_o = green [W_GREEN - 1 -: W_GREEN_O];

        if (W_BLUE_O == W_BLUE)   assign blue_o = blue;
        else if (W_BLUE_O == 1)   assign blue_o = | blue;
        else                      assign blue_o = blue [W_BLUE - 1 -: W_BLUE_O];
    endgenerate

    wire [W_RED_O   - 1:0] red_d   = (GATE && ! display_on) ? '0 : red_o;
    wire [W_GREEN_O - 1:0] green_d = (GATE && ! display_on) ? '0 : green_o;
    wire [W_BLUE_O  - 1:0] blue_d  = (GATE && ! display_on) ? '0 : blue_o;

    logic hsync_d, vsync_d;

    generate
        if (REGISTERED) begin : g_registered
            always_ff @ (posedge clk or posedge rst)
                if (rst)
                begin
                    vga_r <= '0;
                    vga_g <= '0;
                    vga_b <= '0;
                    vsync <= '0;
                    hsync <= '0;
                end
                else
                begin
                    vga_r <= red_d;
                    vga_g <= green_d;
                    vga_b <= blue_d;
                    vsync <= vsync_d;
                    hsync <= hsync_d;
                end
        end else begin : g_combinational
            assign vga_r = red_d;
            assign vga_g = green_d;
            assign vga_b = blue_d;
            assign hsync = hsync_d;
            assign vsync = vsync_d;
        end
    endgenerate

    // Derived constants

    localparam H_SYNC_START  = H_DISPLAY    + H_FRONT + N_MIXER_PIPE_STAGES,
               H_SYNC_END    = H_SYNC_START + H_SYNC  - 1,
               H_MAX         = H_SYNC_END   + H_BACK,

               V_SYNC_START  = V_DISPLAY    + V_BOTTOM,
               V_SYNC_END    = V_SYNC_START + V_SYNC  - 1,
               V_MAX         = V_SYNC_END   + V_TOP;

    // Calculating next values of the counters

    logic [HPOS_WIDTH - 1:0] d_hpos;
    logic [VPOS_WIDTH - 1:0] d_vpos;

    always_comb
    begin
        if (hpos == H_MAX)
        begin
            d_hpos = 1'd0;

            if (vpos == V_MAX)
                d_vpos = 1'd0;
            else
                d_vpos = vpos + 1'd1;
        end
        else
        begin
          d_hpos = hpos + 1'd1;
          d_vpos = vpos;
        end
    end

    // Enable to divide clock from 50 or 100 MHz to 25 MHz

    logic [3:0] clk_en_cnt;
    logic clk_en;

    assign pixel_clk = (CLK_MHZ >= 2 * PIXEL_MHZ) ? clk_en : clk;

    always_ff @ (posedge clk or posedge rst)
    begin
        if (rst)
        begin
            clk_en_cnt <= 3'b0;
            clk_en <= 1'b0;
        end
        else
        begin
            if (clk_en_cnt == (CLK_MHZ / PIXEL_MHZ) - 1)
            begin
                clk_en_cnt <= 3'b0;
                clk_en <= 1'b1;
            end
            else
            begin
                clk_en_cnt <= clk_en_cnt + 1;
                clk_en <= 1'b0;
            end
        end
    end

    // Making all outputs registered

    always_ff @ (posedge clk or posedge rst)
    begin
        if (rst)
        begin
            hsync_d     <= 1'b0;
            vsync_d     <= 1'b0;
            display_on  <= 1'b0;
            hpos        <= 1'b0;
            vpos        <= 1'b0;
        end
        else if (clk_en)
        begin
            hsync_d     <= ~ (    d_hpos >= H_SYNC_START
                               && d_hpos <= H_SYNC_END   );

            vsync_d     <= ~ (    d_vpos >= V_SYNC_START
                               && d_vpos <= V_SYNC_END   );

            display_on  <=   (    d_hpos <  H_DISPLAY
                               && d_vpos <  V_DISPLAY    );

            hpos        <= d_hpos;
            vpos        <= d_vpos;
        end
    end

endmodule
