// =============================================================================
// diff_obuf — one differential output pair (TMDS / LVDS) behind one vendor
// primitive, selected by the VENDOR parameter that codegen supplies from the
// board's device family (`context.diff_buf`):
//
//   "gowin_tlvds"  TLVDS_OBUF  — true LVDS on Gowin Arora (GW2A/GW2AR/GW5),
//                                BGM tang_primer_20k_dock_hdmi_tm1638_yosys
//   "gowin_elvds"  ELVDS_OBUF  — emulated LVDS on Gowin LittleBee (GW1N*),
//                                what Gowin's DVI_TX IP uses on the Tang Nano 9K
//   "xilinx"       OBUFDS      — 7-series, IOSTANDARD from the XDC (TMDS_33),
//                                BGM a7_lite_35t
//   "generic"      o = i, ob = ~i — pseudo-differential on two single-ended
//                                pins (BGM tang_nano_9k_hdmi_no_ip_tm1638's
//                                OBUFDS.v does exactly this)
// =============================================================================

module diff_obuf
# (
    parameter VENDOR = "generic"
)
(
    input  i,
    output o,
    output ob
);

    generate
        if (VENDOR == "gowin_tlvds") begin : g_tlvds
            TLVDS_OBUF u_buf (.I (i), .O (o), .OB (ob));
        end else if (VENDOR == "gowin_elvds") begin : g_elvds
            ELVDS_OBUF u_buf (.I (i), .O (o), .OB (ob));
        end else if (VENDOR == "xilinx") begin : g_xilinx
            OBUFDS # (.IOSTANDARD ("DEFAULT"), .SLEW ("SLOW")) u_buf (.I (i), .O (o), .OB (ob));
        end else begin : g_generic
            assign o  =   i;
            assign ob = ~ i;
        end
    endgenerate

endmodule
