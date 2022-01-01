// Generated from OpenRAM

module reram_r_32_w_64 (
    input [63:0] data,
    input [63:0] mask,
    input csb,
    input web,
    input clk,
    input sense_trig,
    input vref,
    input vclamp,
    input vclampp,
    output [63:0] data_out,
    inout vdd_write,
    inout vdd_wordline,
    input [4:0] addr,
    inout vdd,
    inout gnd,
);
