// Generated from OpenRAM

module reram_sram1 (
    INPUT [6:0] addr,
    INPUT [3:0] bank_sel_b,
    INPUT clk,
    INPUT [4:0] data,
    INPUT data_others,
    OUTPUT [4:0] data_out,
    INOUT gnd,
    INPUT [4:0] mask,
    INPUT mask_others,
    INPUT sense_trig,
    INPUT vclamp,
    INPUT vclampp,
    INOUT vdd,
    INOUT vdd_wordline,
    INOUT vdd_write_bl,
    INOUT vdd_write_br,
    INPUT vref,
    INPUT web,
);
