#!/usr/bin/env python3
from importlib import reload
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from testutils import OpenRamTest
else:
    class OpenRamTest:
        pass


class BankTestBase(OpenRamTest):

    def local_check(self, a, final_verification=False):
        if hasattr(a.wordline_driver, "add_body_taps"):
            a.wordline_driver.add_body_taps()
        super().local_check(a, final_verification)

    # def test_baseline_array(self):
    #     # self.sweep_all(cols=[], rows=[64], words_per_row=1, default_col=256)
    #     self.sweep_all()

    @staticmethod
    def get_bank_class():
        from globals import OPTS
        if hasattr(OPTS, "bank_class"):
            from base.design import design
            return design.import_mod_class_from_str(OPTS.bank_class), {}

        from modules.baseline_bank import BaselineBank
        bank_class = BaselineBank
        return bank_class, {}

    def sweep_all(self, rows=None, cols=None, words_per_row=None, default_row=64, default_col=64):
        import tech
        from base import design

        bank_class, kwargs = self.get_bank_class()

        # tech.drc_exceptions[bank_class.__name__] = tech.drc_exceptions["min_nwell"] + tech.drc_exceptions["latchup"]
        tech.drc_exceptions[bank_class.__name__] = tech.drc_exceptions.get("latchup", [])

        if rows is None:
            rows = [16, 32, 64, 128, 256]
        if cols is None:
            cols = [32, 64, 128, 256]

        try:
            col = default_col
            for row in rows:
                for words_per_row_ in self.get_words_per_row(col, words_per_row):
                    reload(design)
                    self.debug.info(1, "Test {} single bank row = {} col = {} words_per_row = {}".
                                    format(bank_class.__name__, row, col, words_per_row_))
                    word_size = int(col / words_per_row_)
                    num_words = row * words_per_row_
                    a = bank_class(word_size=word_size, num_words=num_words, words_per_row=words_per_row_,
                                   name="bank1", **kwargs)

                    self.local_check(a)
            row = default_row
            for col in cols:
                if col == default_col:
                    continue
                for words_per_row_ in self.get_words_per_row(col, words_per_row):
                    reload(design)
                    self.debug.info(1, "Test {} single bank row = {} col = {} words_per_row = {}".
                                    format(bank_class.__name__, row, col, words_per_row_))
                    word_size = int(col / words_per_row_)
                    num_words = row * words_per_row_
                    a = bank_class(word_size=word_size, num_words=num_words,
                                   words_per_row=words_per_row_,
                                   name="bank1")
                    self.local_check(a)
        except Exception as ex:
            self.debug.error("Failed {} for row = {} col = {}: {} ".format(
                bank_class.__name__, row, col, str(ex)), 0)
            raise ex

    def test_chip_sel(self):
        """Test for chip sel: Two independent banks"""
        from globals import OPTS
        bank_class, kwargs = self.get_bank_class()
        OPTS.route_control_signals_left = True
        OPTS.independent_banks = True
        OPTS.num_banks = 1
        a = bank_class(word_size=8, num_words=16, words_per_row=1,
                       name="bank1", **kwargs)
        self.local_check(a)

    # def test_intra_array_control_signals_rails(self):
    #     """Test for control rails within peripherals arrays but not centralized
    #         (closest to driver pin)"""
    #     from globals import OPTS
    #     bank_class, kwargs = self.get_bank_class()
    #     OPTS.route_control_signals_left = False
    #     OPTS.num_banks = 1
    #     OPTS.centralize_control_signals = False
    #     a = bank_class(word_size=64, num_words=64, words_per_row=1,
    #                    name="bank1", **kwargs)
    #     self.local_check(a)
    #
    # def test_intra_array_centralize_control_signals_rails(self):
    #     """Test for when control rails are centralized in between bitcell array"""
    #     from globals import OPTS
    #     bank_class, kwargs = self.get_bank_class()
    #     OPTS.route_control_signals_left = False
    #     OPTS.num_banks = 1
    #     OPTS.centralize_control_signals = True
    #     a = bank_class(word_size=64, num_words=64, words_per_row=1,
    #                    name="bank1", **kwargs)
    #     self.local_check(a)
    #
    # def test_intra_array_wide_control_buffers(self):
    #     """Test for when control buffers width is greater than bitcell array width"""
    #     from globals import OPTS
    #     bank_class, kwargs = self.get_bank_class()
    #     OPTS.route_control_signals_left = False
    #     OPTS.num_banks = 1
    #     OPTS.control_buffers_num_rows = 1
    #     OPTS.centralize_control_signals = False
    #     a = bank_class(word_size=16, num_words=64, words_per_row=1,
    #                    name="bank1", **kwargs)
    #     self.assertTrue(a.control_buffers.width > a.bitcell_array.width,
    #                     "Adjust word size such that control buffers is wider than bitcell array")
    #     self.local_check(a)
