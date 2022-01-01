#!/usr/bin/env python
from importlib import reload
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from testutils import OpenRamTest
else:
    class OpenRamTest:
        pass


class SramTestBase(OpenRamTest):

    # def test_baseline_array(self):
    #     # self.sweep_all(cols=[], rows=[128], words_per_row=2, default_col=64, num_banks=1)
    #     self.sweep_all()

    def get_sram_class(self):
        from globals import OPTS

        if hasattr(OPTS, "sram_class"):
            sram_class = self.load_class_from_opts("sram_class")
        else:
            from modules.baseline_sram import BaselineSram
            from modules.mram.sotfet.sotfet_mram import SotfetMram
            if hasattr(OPTS, "mram") and OPTS.mram in ["sotfet", "sot"]:
                sram_class = SotfetMram
            else:
                sram_class = BaselineSram
        import tech
        tech.drc_exceptions[sram_class.__name__] = tech.drc_exceptions.get("active_density", [])
        return sram_class

    def sweep_all(self, rows=None, cols=None, words_per_row=None, default_row=64,
                  default_col=64, num_banks=1):

        sram_class = self.get_sram_class()

        if rows is None:
            rows = [16, 32, 64, 128, 256]
        if cols is None:
            cols = [32, 64, 128, 256]

        try:
            col = default_col
            for row in rows:
                for words_per_row_ in self.get_words_per_row(col, words_per_row):
                    self.create_and_test_sram(sram_class, row, col, words_per_row_, num_banks)
            row = default_row
            for col in cols:
                if col == default_col:
                    continue
                for words_per_row_ in self.get_words_per_row(col, words_per_row):
                    self.create_and_test_sram(sram_class, row, col, words_per_row_, num_banks)
        except ZeroDivisionError as ex:
            self.debug.error("Failed {} for row = {} col = {}: {} ".format(
                sram_class.__name__, row, col, str(ex)), self.debug.ERROR_CODE)
            raise ex

    def create_and_test_sram(self, sram_class, num_rows, num_cols, words_per_row, num_banks):
        self.debug.info(1, "Test {} row = {} col = {} words_per_row = {} num_banks = {}".
                        format(sram_class.__name__, num_rows, num_cols, words_per_row, num_banks))
        from base import design
        word_size = int(num_cols / words_per_row)
        num_words = num_rows * words_per_row * num_banks
        reload(design)
        a = sram_class(word_size=word_size, num_words=num_words, words_per_row=words_per_row,
                       num_banks=num_banks, name="sram1", add_power_grid=True)

        self.local_check(a)

    def test_one_bank(self):
        sram_class = self.get_sram_class()
        from globals import OPTS
        OPTS.run_optimizations = False
        OPTS.alu_word_size = 32
        self.create_and_test_sram(sram_class, 32, 64, words_per_row=1, num_banks=1)

    # def test_two_dependent_banks(self):
    #     from globals import OPTS
    #     OPTS.independent_banks = False
    #     sram_class = self.get_sram_class()
    #     for words_per_row in [1, 2, 4, 8]:
    #         self.create_and_test_sram(sram_class, 64, 64, words_per_row=words_per_row, num_banks=2)
    #
    # def test_two_independent_banks(self):
    #     from globals import OPTS
    #     OPTS.independent_banks = True
    #     sram_class = self.get_sram_class()
    #     for words_per_row in [1, 2, 4, 8]:
    #         self.create_and_test_sram(sram_class, 64, 64, words_per_row=words_per_row, num_banks=2)
