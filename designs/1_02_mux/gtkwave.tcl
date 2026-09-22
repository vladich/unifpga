# gtkwave::loadFile "dump.vcd"

set all_signals [list]

lappend all_signals tb.sel
lappend all_signals tb.a
lappend all_signals tb.b
lappend all_signals tb.btn
lappend all_signals tb.led
lappend all_signals {tb.led[0]}
lappend all_signals tb.i_design_top.all_muxes

set num_added [ gtkwave::addSignalsFromList $all_signals ]

gtkwave::/Time/Zoom/Zoom_Full
