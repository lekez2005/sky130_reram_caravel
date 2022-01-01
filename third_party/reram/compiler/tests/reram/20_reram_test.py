#!/usr/bin/env python3
import os
from unittest import skipIf
import math

from reram_test_base import ReRamTestBase

word_size = 64
num_rows = 32
words_per_row = 1
num_banks = 1

num_words = num_banks * words_per_row * num_rows
address_width = int(math.log2(num_words))

module_name = f"r_{num_words}_w_{word_size}"

generate_reram_gds = True
skip_ram_lvs = True
skip_copy = False

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
base_dir = "/research/APSEL/ota2/skywater/openlane_workspace/caravel_reram"
print(base_dir)

gds_dir = os.path.join(base_dir, "gds")
verilog_dir = os.path.join(base_dir, "verilog", "rtl")


gds_file = os.path.join(gds_dir, f"{module_name}.gds")
output_spice = os.path.join(base_dir, "netgen", f"{module_name}.spice")


class ReRamTest(ReRamTestBase):

    # @skipIf(generate_reram_gds, "Skipping reram gds generation")
    def test_wrap_reram(self):
        from caravel_wrapper import wrap_reram
        a = wrap_reram(gds_file)
        self.local_drc_check(a)


    @skipIf(not generate_reram_gds, "Skipping reram gds generation")
    def test_one_bank(self):
        a = self.create_class_from_opts("sram_class", word_size=word_size,
                                        num_words=num_words,
                                        words_per_row=words_per_row,
                                        num_banks=num_banks,
                                        name="sram1",
                                        add_power_grid=True)
        if not skip_ram_lvs:
            self.local_check(a)

        a.sp_write(output_spice)
        a.gds_write(gds_file)
        self.generate_verilog(a)

    def generate_verilog(self, sram):
        file_name = os.path.join(verilog_dir, f"{module_name}.v")
        with open(file_name, "w") as f:
            f.write(f"// Generated from OpenRAM\n\n")
            f.write(f"module reram_{module_name} (\n")

            inputs = ["sense_trig", "vref", "vclamp", "vclampp", "csb", "web", "clk"]

            prefixes = {
                "data[": ("input", word_size),
                "mask[": ("input", word_size),
                "data_out[": ("output", word_size),
                "addr[": ("input", address_width)
            }

            processed_keys = set()

            for pin in sram.pins:
                pin = pin.lower()
                if pin in inputs:
                    pin_type = "input"
                elif pin in ["vdd", "gnd", "vdd_write", "vdd_wordline"]:
                    pin_type = "inout"
                else:
                    for prefix in prefixes:
                        if pin.startswith(prefix):
                            pin_type = prefixes[prefix][0]
                            width = prefixes[prefix][1]
                            pin = f"[{width - 1}:0] {prefix[:-1]}"

                if not pin_type:
                    self.fail(f"Pin type for {pin} not specified")
                if pin in processed_keys:
                    continue
                processed_keys.add(pin)
                f.write(f"    {pin_type} {pin},\n")

            f.write(f");\n")


ReRamTest.run_tests(__name__)
