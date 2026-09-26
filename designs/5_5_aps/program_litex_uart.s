# RV32I firmware fixture for tb_litex_uart.sv. program_litex_uart.hex has the
# corresponding machine words, checked through CPU execution in that test.
# APS slot 6 is the LiteX-backed fixed 115200 8N1 TX in this variant.
.text
    lui  t0, 0x08000       # timer at 0x08000000
    addi t1, zero, 100     # wait for 100 real 10 MHz ticks
wait:
    lw   t2, 0(t0)
    bltu t2, t1, wait
    lui  t3, 0x02000       # LED at 0x02000000
    addi t4, zero, 1
    sw   t4, 0(t3)
    lui  t5, 0x06000       # UART TX at 0x06000000
    addi t6, zero, 0x55
    addi t4, zero, 0xa5
    sw   t6, 0(t5)
    sw   t4, 0(t5)         # consecutive stores exercise bus backpressure
done:
    jal  zero, done
