# gtkwave::loadFile "dump.vcd"

set all_signals [list]

lappend all_signals tb.clk
lappend all_signals tb.rst
lappend all_signals tb.btn
lappend all_signals tb.i_design_top.cnt
lappend all_signals tb.i_design_top.led
lappend all_signals tb.i_design_top.abcdefgh
lappend all_signals tb.i_design_top.digit

set num_added [ gtkwave::addSignalsFromList $all_signals ]

gtkwave::/Time/Zoom/Zoom_Full
