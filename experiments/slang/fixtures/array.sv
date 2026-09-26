module array_leaf(input logic a, output logic b);
  assign b = a;
endmodule

module array_top(input logic [1:0] a, output logic [1:0] b, output wire gate_q);
  array_leaf lane[1:0](.a(a), .b(b));
  and gate1(gate_q, a[0], a[1]);
endmodule
