import datetime
import math
import re

import debug
import tech
from base import utils
from base.contact import m1m2, m2m3, cross_m2m3, cross_m1m2, m3m4
from base.contact_full_stack import ContactFullStack
from base.design import METAL1, METAL2, METAL3, METAL4, METAL5, design, PWELL, ACTIVE, NIMP, PIMP
from base.geometry import NO_MIRROR, MIRROR_Y_AXIS
from base.vector import vector
from base.well_implant_fills import create_wells_and_implants_fills, get_default_fill_layers
from globals import OPTS, print_time
from modules.baseline_bank import BaselineBank
from modules.flop_buffer import FlopBuffer
from modules.hierarchical_predecode2x4 import hierarchical_predecode2x4
from modules.hierarchical_predecode3x8 import hierarchical_predecode3x8


class BaselineSram(design):
    wide_space = None
    bank_insts = bank = row_decoder = None
    column_decoder = column_decoder_inst = None
    row_decoder_inst = None

    def __init__(self, word_size, num_words, num_banks, name, words_per_row=None,
                 add_power_grid=True):
        """Words will be split across banks in case of two banks"""
        assert num_banks in [1, 2], "Only one or two banks supported"
        if num_banks == 2 and not OPTS.independent_banks:
            assert word_size % 2 == 0, "Word-size must be even when word is spread across two banks"
            word_size = int(word_size / 2)
        if words_per_row is not None:
            assert words_per_row in [1, 2, 4, 8], "Max 8 words per row supported"

        self.add_power_grid = add_power_grid

        start_time = datetime.datetime.now()

        design.__init__(self, name)

        self.bitcell = self.create_mod_from_str(OPTS.bitcell)

        self.compute_sizes(word_size, num_words, num_banks, words_per_row)
        debug.info(2, "create sram of size {0} with {1} num of words".format(self.word_size,
                                                                             self.num_words))
        self.create_layout()

        self.offset_all_coordinates()
        sizes = self.find_highest_coords()
        self.width = sizes[0]
        self.height = sizes[1]
        self.add_boundary()

        self.DRC_LVS(final_verification=True)

        if not OPTS.is_unit_test:
            print_time("SRAM creation", datetime.datetime.now(), start_time)

        # restore word-size
        if num_banks == 2 and not OPTS.independent_banks:
            self.word_size = 2 * self.word_size

    def create_layout(self):
        self.single_bank = self.num_banks == 1
        self.wide_space = self.get_wide_space(METAL1)
        self.m1_pitch = self.m1_width + self.get_parallel_space(METAL1)
        self.m2_pitch = self.m2_width + self.get_parallel_space(METAL2)
        self.m3_pitch = self.m3_width + self.get_parallel_space(METAL3)
        self.create_modules()
        self.add_modules()
        self.add_pins()

        self.min_point = min(self.row_decoder_inst.by(), self.bank_insts[0].by())

        self.route_layout()

    def create_modules(self):
        debug.info(1, "Create sram modules")
        self.create_bank()
        self.row_decoder = self.bank.decoder
        self.min_point = self.bank.min_point
        self.fill_width = self.bank.fill_width
        self.fill_height = self.bank.fill_height
        self.row_decoder_y = self.bank.bitcell_array_inst.uy() - self.row_decoder.height
        self.create_column_decoder()

        bank_flop_connections = [x[0] for x in
                                 self.bank.get_control_flop_connections().values()]
        control_inputs = (self.bank.get_non_flop_control_inputs() +
                          ["clk"] + bank_flop_connections)
        self.control_inputs = control_inputs
        self.bank_flop_inputs = bank_flop_connections

    def add_modules(self):
        debug.info(1, "Add sram modules")
        self.right_bank_inst = self.bank_inst = self.add_bank(0, vector(0, 0))
        self.bank_insts = [self.right_bank_inst]
        self.add_col_decoder()
        self.add_row_decoder()
        self.add_power_rails()
        if self.num_banks == 2:
            x_offset = self.get_left_bank_x()
            # align bitcell array
            y_offset = self.right_bank_inst.by() + (self.bank.bitcell_array_inst.by() -
                                                    self.left_bank.bitcell_array_inst.by())
            self.left_bank_inst = self.add_bank(1, vector(x_offset, y_offset))
            self.bank_insts = [self.right_bank_inst, self.left_bank_inst]

    def route_layout(self):
        debug.info(1, "Route sram")
        self.fill_decoder_wordline_space()
        self.route_column_decoder()
        self.route_row_decoder_clk()
        self.route_decoder_power()
        self.route_decoder_outputs()
        self.join_bank_controls()

        self.route_left_bank_power()

        self.copy_layout_pins()
        self.route_power_grid()

    def compute_sizes(self, word_size, num_words, num_banks, words_per_row):
        self.num_banks = num_banks
        self.num_words = num_words
        self.word_size = word_size
        OPTS.num_banks = num_banks

        if self.num_banks == 2 and OPTS.independent_banks:
            self.num_words_per_bank = int(num_words / num_banks)
        else:  # for non-independent banks, put half word per bank, total number of words remains the same
            self.num_words_per_bank = num_words

        if words_per_row is None:
            words_per_row = self.estimate_words_per_row(word_size, self.num_words_per_bank)

        self.words_per_row = words_per_row
        self.num_rows = int(self.num_words_per_bank / self.words_per_row)
        self.num_cols = int(self.word_size * self.words_per_row)

        self.col_addr_size = int(math.log(self.words_per_row, 2))
        self.row_addr_size = int(math.log(self.num_rows, 2))
        self.bank_addr_size = self.col_addr_size + self.row_addr_size
        self.addr_size = int(math.log(num_words, 2))

    def estimate_words_per_row(self, word_size, num_words):
        area = math.inf
        all_words_per_row = [1, 2, 4, 8]
        for i in range(len(all_words_per_row)):
            words_per_row = all_words_per_row[i]
            if not num_words % words_per_row == 0:  # not divisible
                return all_words_per_row[i - 1]
            num_rows = num_words / words_per_row
            # heuristic extra 16 for decoder/wordline, extra 25 for peripherals below array
            tentative_area = (((words_per_row * word_size + 16) * self.bitcell.width) *
                              ((num_rows + 25) * self.bitcell.height))
            if tentative_area > area:  # previous config has lower area, terminate
                return all_words_per_row[i - 1]
            else:
                area = tentative_area
        return all_words_per_row[-1]

    @staticmethod
    def get_bank_class():
        if hasattr(OPTS, "bank_class"):
            return design.import_mod_class_from_str(OPTS.bank_class)
        return BaselineBank

    def create_bank(self):
        bank_class = self.get_bank_class()
        self.bank = bank_class(name="bank", word_size=self.word_size, num_words=self.num_words_per_bank,
                               words_per_row=self.words_per_row, num_banks=self.num_banks)
        self.add_mod(self.bank)
        if self.num_banks == 2:
            debug.info(1, "Creating left bank")
            self.left_bank = bank_class(name="left_bank", word_size=self.word_size,
                                        num_words=self.num_words_per_bank,
                                        words_per_row=self.words_per_row,
                                        num_banks=self.num_banks,
                                        adjacent_bank=self.bank)
            self.add_mod(self.left_bank)

    def add_bank(self, bank_num, position):
        if bank_num == 0:
            bank_mod = self.bank
            mirror = NO_MIRROR
        else:
            bank_mod = self.left_bank
            mirror = MIRROR_Y_AXIS
            position.x += bank_mod.width
        bank_inst = self.add_inst(name="bank{0}".format(bank_num),
                                  mod=bank_mod,
                                  offset=position,
                                  mirror=mirror)

        self.connect_inst(self.get_bank_connections(bank_num, bank_mod))
        return bank_inst

    @staticmethod
    def create_column_decoder_modules(words_per_row):
        if words_per_row == 2:
            column_decoder = FlopBuffer(OPTS.control_flop, OPTS.column_decoder_buffers)
        else:
            col_buffers = OPTS.column_decoder_buffers
            buffer_sizes = [OPTS.predecode_sizes[0]] + col_buffers
            decoder_class = hierarchical_predecode2x4 \
                if words_per_row == 4 else hierarchical_predecode3x8
            column_decoder = decoder_class(use_flops=True, buffer_sizes=buffer_sizes,
                                           negate=False)
        return column_decoder

    def get_col_decoder_connections(self):
        col_address_bits = [i + self.row_addr_size for i in reversed(range(self.col_addr_size))]
        col_decoder_connections = []
        for i in col_address_bits:
            col_decoder_connections.append("ADDR[{}]".format(i))
        for i in range(self.words_per_row):
            col_decoder_connections.append("sel[{}]".format(i))
        col_decoder_connections.extend(["decoder_clk", "vdd", "gnd"])
        return col_decoder_connections

    def create_column_decoder(self):
        if self.words_per_row < 2:
            return
        self.column_decoder = self.create_column_decoder_modules(self.words_per_row)
        if self.words_per_row == 2:
            # Export internal flop output as layout pin, rearrange pin to match predecoder order
            self.column_decoder.pins = ["din", "dout_bar", "dout", "clk", "vdd", "gnd"]
            col_decoder_buffer = self.column_decoder.buffer_inst.mod
            if len(col_decoder_buffer.module_insts) > 1:
                col_decoder_buffer.copy_layout_pin(col_decoder_buffer.module_insts[-1], "A",
                                                   "out_buf_input")
                self.column_decoder.copy_layout_pin(self.column_decoder.buffer_inst, "out_buf_input",
                                                    "dout_bar")
            else:
                self.column_decoder.copy_layout_pin(self.column_decoder.buffer_inst, "in",
                                                    "dout_bar")

        self.add_mod(self.column_decoder)

    def get_schematic_pins(self):
        pins = []
        if self.num_banks == 2 and not OPTS.independent_banks:
            word_size = self.word_size * 2
        else:
            word_size = self.word_size
        for i in range(word_size):
            pins.append("DATA[{0}]".format(i))
            if self.bank.has_mask_in:
                pins.append("MASK[{0}]".format(i))
        # pins for the other independent bank
        if OPTS.independent_banks and self.num_banks == 2:
            for i in range(word_size):
                pins.append("DATA_1[{0}]".format(i))
                if self.bank.has_mask_in:
                    pins.append("MASK_1[{0}]".format(i))

        for i in range(self.bank_addr_size):
            pins.append("ADDR[{0}]".format(i))

        replacements_list = [x[:2] for x in self.get_bank_connection_replacements()]
        replacements = {key: val for key, val in replacements_list}
        self.control_pin_names = []
        for pin_name in self.control_inputs:
            new_pin_name = replacements.get(pin_name, pin_name)
            self.control_pin_names.append(new_pin_name)
            pins.append(new_pin_name)

        pins.extend(["vdd", "gnd"])
        return pins

    def add_pins(self):
        self.add_pin_list(self.get_schematic_pins())

    def copy_layout_pins(self):

        replacements_list = [x[:2] for x in self.get_bank_connection_replacements()]
        replacements = {key: val for key, val in replacements_list}

        right_bank = self.bank_insts[0]
        for pin_name in self.control_inputs:
            new_pin_name = replacements.get(pin_name, pin_name)
            if self.num_banks == 1:
                self.copy_layout_pin(right_bank, pin_name, new_pin_name)
            else:
                rail = getattr(self, pin_name + "_rail")
                self.add_layout_pin(new_pin_name, METAL3, vector(rail.cx(), rail.by()),
                                    width=rail.height, height=rail.height)

        for i in range(self.row_addr_size):
            self.copy_layout_pin(self.row_decoder_inst, "A[{}]".format(i), "ADDR[{}]".format(i))

        # copy DATA and MASK pins
        for i in range(self.num_banks):
            bank_inst = self.bank_insts[i]
            bank_connections = None
            for index, inst in enumerate(self.insts):
                if inst.name == bank_inst.name:
                    bank_connections = self.conns[index]
                    break
            for pin_index, net in enumerate(bank_connections):
                if net.startswith("DATA") or net.startswith("MASK"):
                    pin_name = bank_inst.mod.pins[pin_index]
                    self.copy_layout_pin(bank_inst, pin_name, net)

    def add_col_decoder(self):
        if self.words_per_row == 1:
            return
        lowest_control_flop = min(self.bank.control_flop_insts, key=lambda x: x[2].by())[2]
        if self.bank.col_decoder_is_left:
            flop_inputs_pins = [self.right_bank_inst.get_pin(x)
                                for x in self.bank_flop_inputs]
            left_most_rail_x = min(flop_inputs_pins, key=lambda x: x.lx()).lx() - self.bus_space
        else:
            left_most_rail_x = self.bank.leftmost_control_rail.offset.x

        column_decoder_y = (lowest_control_flop.by() +
                            (self.bank.col_decoder_y - self.bank.control_flop_y))  # account for offset_all_coordinates

        column_decoder = self.column_decoder

        col_decoder_x = (left_most_rail_x - (1 + self.words_per_row) * self.bus_pitch -
                         column_decoder.width)
        self.column_decoder_inst = self.add_inst("col_decoder", mod=self.column_decoder,
                                                 offset=vector(col_decoder_x, column_decoder_y))
        self.connect_inst(self.get_col_decoder_connections())

    def get_left_bank_x(self):
        x_offset_by_wordline_driver = self.mid_gnd.lx() - self.wide_space - self.bank.width
        # find max control rail offset
        rail_offsets = [getattr(self.bank, rail_name + "_rail").lx() for rail_name in self.bank.rail_names]
        min_rail_x = min(rail_offsets)
        col_to_row_decoder_space = (self.words_per_row + 1) * self.m2_pitch
        if self.column_decoder_inst is not None:
            x_offset_by_col_decoder = (self.column_decoder_inst.lx() - col_to_row_decoder_space -
                                       (self.bank.width - min_rail_x))
            return min(x_offset_by_wordline_driver, x_offset_by_col_decoder)
        return x_offset_by_wordline_driver

    def add_row_decoder(self):

        top_flop_inst = max(self.bank.control_flop_insts, key=lambda x: x[2].by())[2]
        min_y = self.row_decoder_y
        if self.bank.col_decoder_is_left:
            self.col_sel_rails_y = (top_flop_inst.uy() + self.bank.rail_space_above_controls
                                    - self.words_per_row * self.bus_pitch)
            min_y = min(min_y, self.col_sel_rails_y)
        elif self.words_per_row > 1:
            sel_conns = self.conns[self.insts.index(self.column_decoder_inst)]
            sel_pin_indices = [index for index, net in enumerate(sel_conns) if "sel" in net]
            sel_pin_names = [self.column_decoder_inst.mod.pins[i] for i in sel_pin_indices]
            sel_pins = [self.column_decoder_inst.get_pin(x) for x in sel_pin_names]
            min_y = min(min_y, min(sel_pins, key=lambda x: x.by()).by())

        bank_m2_rails = self.bank.m2_rails
        valid_rails = [x for x in bank_m2_rails if x.uy() + self.get_line_end_space(METAL2) > min_y]
        # always include decoder clk
        leftmost_rail = min(valid_rails + [self.get_decoder_clk_pin()], key=lambda x: x.lx())
        left_most_rail_x = leftmost_rail.lx()
        if self.column_decoder_inst is not None:
            left_most_rail_x -= self.words_per_row * self.bus_pitch + self.bus_space
        self.leftmost_m2_rail_x = left_most_rail_x

        max_predecoder_x = (left_most_rail_x - self.get_wide_space(METAL2) -
                            self.row_decoder.width)
        max_row_decoder_x = self.bank.wordline_driver_inst.lx() - self.row_decoder.row_decoder_width
        x_offset = min(max_predecoder_x, max_row_decoder_x)

        self.row_decoder_inst = self.add_inst(name="row_decoder", mod=self.row_decoder,
                                              offset=vector(x_offset, self.row_decoder_y))

        self.connect_inst(self.get_row_decoder_connections())

    def get_row_decoder_connections(self):
        temp = []
        for i in range(self.row_addr_size):
            temp.append("ADDR[{0}]".format(i))
        for j in range(self.num_rows):
            temp.append("dec_out[{0}]".format(j))
        temp.extend(["decoder_clk", "vdd", "gnd"])
        return temp

    def add_power_rails(self):
        bank_vdd = self.bank.mid_vdd
        y_offset = bank_vdd.by()
        min_decoder_x = self.row_decoder_inst.lx()
        if self.column_decoder_inst is not None:
            min_decoder_x = min(min_decoder_x, self.column_decoder_inst.lx() - 2 * self.bus_pitch)
        x_offset = min_decoder_x - self.wide_space - bank_vdd.width()
        self.mid_vdd = self.add_rect(METAL2, offset=vector(x_offset, y_offset), width=bank_vdd.width(),
                                     height=bank_vdd.height())

        x_offset -= (self.bank.wide_power_space + bank_vdd.width())
        self.mid_gnd = self.add_rect(METAL2, offset=vector(x_offset, y_offset), width=bank_vdd.width(),
                                     height=bank_vdd.height())

    @staticmethod
    def shift_bits(prefix, bit_shift, conns_):
        for index, conn in enumerate(conns_):
            pattern = r"{}\[([0-9]+)\]".format(prefix)
            match = re.match(pattern, conn)
            if match:
                digit = int(match.group(1))
                conns_[index] = "{}[{}]".format(prefix, bit_shift + digit)

    def get_bank_connection_replacements(self):
        address_msb = "ADDR[{}]".format(self.addr_size - 1)
        return [
            ("read", "Web"),
            ("addr_msb", address_msb),
            ("clk_buf", "decoder_clk")
        ]

    def get_bank_connections(self, bank_num, bank_mod):

        connections = bank_mod. \
            connections_from_mod(bank_mod, self.get_bank_connection_replacements())

        if self.num_banks == 2 and bank_num == 1:
            if OPTS.independent_banks:
                connections = bank_mod.connections_from_mod(connections, [("DATA[", "DATA_1["),
                                                                          ("MASK[", "MASK_1[")])
            else:
                self.shift_bits("DATA", self.word_size, connections)
                self.shift_bits("MASK", self.word_size, connections)
        return connections

    def get_decoder_clk_pin(self):
        clk_pin_name = "decoder_clk" if "decoder_clk" in self.bank.pins else "clk_buf"
        return self.right_bank_inst.get_pin(clk_pin_name)

    def route_row_decoder_clk(self):
        clk_pin = self.get_decoder_clk_pin()

        decoder_clk_pins = self.row_decoder_inst.get_pins("clk")
        valid_decoder_pins = list(filter(lambda x: x.by() > clk_pin.by(), decoder_clk_pins))
        closest_clk = min(valid_decoder_pins, key=lambda x: abs(clk_pin.by() - x.by()))

        predecoder_mod = (self.row_decoder.pre2x4_inst + self.row_decoder.pre3x8_inst)[0]
        predecoder_vdd_height = predecoder_mod.get_pins("vdd")[0].height()
        wide_space = self.get_line_end_space(METAL3)

        # TODO: sky_tapeout: calculate via_offset
        y_offset = (closest_clk.by() + 0.5 * predecoder_vdd_height + wide_space +
                    0.5 * self.m2_space)

        self.add_rect(METAL2, offset=clk_pin.ll(), width=clk_pin.width(),
                      height=y_offset + m1m2.height - clk_pin.by())
        if clk_pin.layer == METAL3:
            self.add_cross_contact_center(cross_m2m3, clk_pin.center())
        via_y = y_offset + 0.5 * m1m2.height
        self.add_contact_center(m1m2.layer_stack, offset=vector(clk_pin.cx(), via_y))
        m1_y = y_offset + 0.5 * m1m2.height - 0.5 * self.m1_width
        self.add_rect(METAL1, offset=vector(closest_clk.lx(), m1_y),
                      width=clk_pin.cx() - closest_clk.lx())
        # TODO: sky_tapeout: calculate via_offset
        via_x = closest_clk.lx() + 0.5 * m1m2.w_2
        self.add_contact_center(m1m2.layer_stack, offset=vector(via_x, via_y))

    def route_column_decoder(self):
        if self.words_per_row < 2:
            return
        if self.words_per_row == 2:
            self.route_flop_column_decoder()
        else:
            self.route_predecoder_column_decoder()

    def route_flop_column_decoder(self):
        self.route_col_decoder_clock()

        # outputs
        out_pin = self.column_decoder_inst.get_pin("dout")
        out_bar_pin = self.column_decoder_inst.get_pin("dout_bar")
        out_bar_y = out_bar_pin.cy()
        out_y = max(out_bar_y, out_pin.cy()) + self.bus_pitch
        y_offsets = [out_bar_y, out_y]
        self.col_decoder_outputs = []

        self.route_col_decoder_to_rail(output_pins=[out_bar_pin, out_pin], rail_offsets=y_offsets)

        self.route_col_decoder_outputs()
        self.route_col_decoder_power()
        self.copy_layout_pin(self.column_decoder_inst, "din", self.get_col_decoder_connections()[0])

    def route_col_decoder_clock(self):
        # route clk
        row_decoder_clk = min(self.row_decoder_inst.get_pins("clk"), key=lambda x: x.cy())
        row_decoder_vdd = min(self.row_decoder_inst.get_pins("vdd"), key=lambda x: x.cy())
        decoder_clk_y = row_decoder_vdd.by() - self.bus_pitch
        self.add_rect(METAL2, offset=vector(row_decoder_clk.lx(), decoder_clk_y), width=row_decoder_clk.width(),
                      height=row_decoder_clk.by() - decoder_clk_y)
        self.add_cross_contact_center(cross_m2m3, offset=vector(row_decoder_clk.cx(),
                                                                decoder_clk_y + 0.5 * self.bus_width))
        x_offset = self.column_decoder_inst.lx() - self.bus_pitch
        self.add_rect(METAL3, offset=vector(x_offset, decoder_clk_y), height=self.bus_width,
                      width=row_decoder_clk.cx() - x_offset)
        col_decoder_clk = self.column_decoder_inst.get_pin("clk")
        self.add_cross_contact_center(cross_m2m3, offset=vector(x_offset + 0.5 * self.bus_width,
                                                                decoder_clk_y + 0.5 * self.bus_width))
        y_offset = col_decoder_clk.uy() - self.bus_width
        self.add_rect(METAL2, offset=vector(x_offset, y_offset), height=decoder_clk_y - y_offset,
                      width=self.bus_width)
        self.add_rect(METAL2, offset=vector(x_offset, y_offset), height=self.bus_width,
                      width=col_decoder_clk.lx() - x_offset)
        if col_decoder_clk.layer == METAL1:
            self.add_contact(m1m2.layer_stack, offset=vector(col_decoder_clk.lx(),
                                                             col_decoder_clk.cy() - 0.5 * m1m2.width),
                             rotate=90)

    def route_col_decoder_to_rail(self, output_pins=None, rail_offsets=None):
        if output_pins is None:
            output_pins = [self.column_decoder_inst.get_pin("out[{}]".format(i)) for i in range(self.words_per_row)]
        if rail_offsets is None:
            rail_offsets = [x.cy() for x in output_pins]

        if self.bank.col_decoder_is_left:
            base_x = self.column_decoder_inst.rx() + self.bus_pitch
            # using by() because mirror
            base_y = self.col_sel_rails_y
            rails_y = [base_y + i * self.bus_pitch for i in range(self.words_per_row)]

            x_offset = self.leftmost_m2_rail_x
            rails_x = [x_offset + i * self.bus_pitch for i in range(self.words_per_row)]
        else:
            base_x = self.leftmost_m2_rail_x
            rails_y = []
            rails_x = []
        x_offsets = [base_x + i * self.bus_pitch for i in range(self.words_per_row)]

        self.col_decoder_outputs = []
        for i in range(self.words_per_row):
            output_pin = output_pins[i]
            x_offset = x_offsets[i]
            y_offset = rail_offsets[i]
            if i == 0 and self.words_per_row == 2:
                if len(self.column_decoder.buffer_inst.mod.module_insts) > 1:
                    self.add_contact_center(m1m2.layer_stack, rotate=90,
                                            offset=vector(output_pin.cx(), y_offset))
                self.add_rect(METAL2, offset=vector(output_pin.lx(), y_offset - 0.5 * self.m2_width),
                              height=self.m2_width,
                              width=x_offset - output_pin.lx() + self.bus_width)
            else:
                self.add_rect(METAL1, offset=vector(output_pin.cx(), y_offset),
                              width=x_offset - output_pin.cx(),
                              height=self.bus_width)
                self.add_cross_contact_center(cross_m1m2,
                                              offset=vector(x_offset + 0.5 * self.bus_width,
                                                            y_offset + 0.5 * self.bus_width),
                                              rotate=True)
            if not self.bank.col_decoder_is_left:
                self.col_decoder_outputs.append(self.add_rect(METAL2,
                                                              offset=vector(x_offset, y_offset),
                                                              width=self.bus_width,
                                                              height=self.bus_width))
            else:
                _, fill_height = self.calculate_min_area_fill(self.bus_width, layer=METAL2)
                rail_y = rails_y[i]
                m2_height = rail_y - y_offset
                self.add_rect(METAL2, offset=vector(x_offset, y_offset),
                              height=m2_height, width=self.bus_width)

                if abs(m2_height) < fill_height:
                    self.add_rect(METAL2, offset=vector(x_offset, y_offset), height=fill_height, width=self.bus_width)

                self.add_cross_contact_center(cross_m2m3, offset=vector(x_offset + 0.5 * self.bus_width,
                                                                        rail_y + 0.5 * self.bus_width))
                self.add_rect(METAL3, offset=vector(x_offset, rail_y), width=rails_x[i] - x_offset,
                              height=self.bus_width)
                self.add_cross_contact_center(cross_m2m3, offset=vector(rails_x[i] + 0.5 * self.bus_width,
                                                                        rail_y + 0.5 * self.bus_width))
                self.col_decoder_outputs.append(self.add_rect(METAL2, offset=vector(rails_x[i], rail_y),
                                                              width=self.bus_width, height=self.bus_width))

    def route_col_decoder_outputs(self):
        if self.num_banks == 2:
            top_predecoder_inst = max(self.row_decoder.pre2x4_inst + self.row_decoder.pre3x8_inst,
                                      key=lambda x: x.uy())
            # place rails just above the input flops
            num_flops = top_predecoder_inst.mod.number_of_inputs
            y_space = top_predecoder_inst.get_pins("vdd")[0].height() + self.get_wide_space(METAL3) + self.bus_space
            self.left_col_mux_select_y = (self.row_decoder_inst.by() + top_predecoder_inst.by()
                                          + num_flops * top_predecoder_inst.mod.flop.height + y_space)

        for i in range(self.words_per_row):
            sel_pin = self.right_bank_inst.get_pin("sel[{}]".format(i))
            rail = self.col_decoder_outputs[i]
            self.add_rect(METAL2, offset=rail.ul(), width=self.bus_width,
                          height=sel_pin.cy() - rail.uy())
            self.add_rect(METAL1, offset=vector(rail.lx(), sel_pin.cy() - 0.5 * self.bus_width),
                          width=sel_pin.lx() - rail.lx(), height=self.bus_width)
            self.add_cross_contact_center(cross_m1m2, offset=vector(rail.cx(), sel_pin.cy()),
                                          rotate=True)

            if self.num_banks == 2:
                # route to the left
                x_start = self.left_bank_inst.rx() - self.left_bank.leftmost_rail.offset.x
                x_offset = x_start + (1 + i) * self.bus_pitch

                y_offset = self.left_col_mux_select_y + i * self.bus_pitch
                self.add_rect(METAL2, offset=vector(rail.lx(), sel_pin.cy()), width=self.bus_width,
                              height=y_offset - sel_pin.cy())
                self.add_cross_contact_center(cross_m2m3,
                                              offset=vector(rail.cx(),
                                                            y_offset + 0.5 * self.bus_width))
                self.add_rect(METAL3, offset=vector(x_offset, y_offset), height=self.bus_width,
                              width=rail.lx() - x_offset)
                self.add_cross_contact_center(cross_m2m3,
                                              offset=vector(x_offset + 0.5 * self.bus_width,
                                                            y_offset + 0.5 * self.bus_width))
                sel_pin = self.left_bank_inst.get_pin("sel[{}]".format(i))
                self.add_rect(METAL2, offset=vector(x_offset, sel_pin.cy()), width=self.bus_width,
                              height=y_offset - sel_pin.cy())
                self.add_cross_contact_center(cross_m1m2,
                                              offset=vector(x_offset + 0.5 * self.bus_width,
                                                            sel_pin.cy()), rotate=True)
                self.add_rect(METAL1, offset=sel_pin.lr(), height=sel_pin.height(),
                              width=x_offset - sel_pin.rx())

    def route_right_bank_sel_in(self, sel_offsets):
        """
        route sel pins from col decoder to the bank on the right
        :param sel_offsets: arranged from sel_0 to sel_x
        """
        y_bend = (self.bank.wordline_driver_inst.by() - self.bank.col_decoder_rail_space +
                  self.m3_pitch)
        x_bend = (self.row_decoder_inst.lx() + self.row_decoder.width +
                  self.words_per_row * self.m2_pitch + 2 * self.wide_space)
        for i in range(len(sel_offsets)):
            in_pin = self.bank_insts[0].get_pin("sel[{}]".format(i))
            x_offset = sel_offsets[i]
            self.add_rect(METAL2, offset=vector(x_offset, in_pin.by()), height=y_bend - in_pin.by())
            self.add_contact(m2m3.layer_stack, offset=vector(x_offset,
                                                             y_bend + self.m3_width - m2m3.height))

            self.add_rect(METAL3, offset=vector(x_offset, y_bend), width=x_bend - x_offset)

            self.add_contact(m2m3.layer_stack, offset=vector(x_bend + m2m3.height, y_bend),
                             rotate=90)
            in_pin = self.bank_insts[0].get_pin("sel[{}]".format(i))
            self.add_rect(METAL2, offset=vector(x_bend, in_pin.by()), height=y_bend - in_pin.by())
            self.add_contact(m1m2.layer_stack, offset=vector(x_bend, in_pin.by()))
            self.add_rect(METAL1, offset=vector(x_bend, in_pin.by()), width=in_pin.lx() - x_bend)

            y_bend += self.m3_pitch
            x_bend -= self.m2_pitch

    def route_predecoder_column_decoder(self):
        self.route_col_decoder_clock()

        # address pins
        all_addr_pins = [x for x in self.get_col_decoder_connections() if x.startswith("ADDR")]
        all_addr_pins = list(reversed(all_addr_pins))
        for i in range(self.col_addr_size):
            self.copy_layout_pin(self.column_decoder_inst, "flop_in[{}]".format(i), all_addr_pins[i])

        #
        self.route_col_decoder_to_rail()
        self.route_col_decoder_outputs()
        self.route_col_decoder_power()

    def route_decoder_power(self):
        rails = [self.mid_vdd, self.mid_gnd]

        sample_power_pin = max(self.row_decoder_inst.get_pins("vdd"), key=lambda x: x.uy())
        m3m4_via = ContactFullStack(start_layer=METAL3, stop_layer=METAL4,
                                    centralize=True, max_width=self.mid_vdd.width,
                                    max_height=sample_power_pin.height())

        pin_names = ["vdd", "gnd"]
        for i in range(2):
            rail = rails[i]
            center_rail_x = 0.5 * (rail.lx() + rail.rx())
            power_pins = self.row_decoder_inst.get_pins(pin_names[i])
            for power_pin in power_pins:
                if power_pin.uy() < self.bank.wordline_driver_inst.by() + \
                        self.bank_inst.by():
                    pin_right = power_pin.rx()
                    x_offset = rail.lx()
                else:
                    pin_right = self.bank.wordline_driver_inst.lx()
                    x_offset = rail.lx() if self.single_bank else self.left_bank_inst.rx()
                self.add_rect(power_pin.layer, offset=vector(x_offset, power_pin.by()),
                              width=pin_right - x_offset, height=power_pin.height())
                if power_pin.layer == METAL1:
                    vias = [m1m2]
                    sizes = [[1, 2]]
                else:
                    vias = [m2m3, m3m4]
                    sizes = [[1, 2], m3m4_via.dimensions]
                for via, size in zip(vias, sizes):
                    self.add_contact_center(via.layer_stack,
                                            offset=vector(center_rail_x, power_pin.cy()),
                                            size=size, rotate=90)

        via_offsets, fill_height = self.evaluate_left_power_rail_vias()
        self.add_left_power_rail_vias(via_offsets, self.mid_vdd.uy(), fill_height)

    def evaluate_left_power_rail_vias(self):
        # find locations for
        fill_width = self.mid_vdd.width
        _, fill_height = self.calculate_min_area_fill(fill_width, min_height=self.m3_width,
                                                      layer=METAL3)

        wide_space = self.get_wide_space(METAL3)
        via_spacing = wide_space + self.parallel_via_space
        via_pitch = via_spacing + max(m2m3.height, fill_height)

        m2_m3_blockages = []

        for pin_name in ["vdd", "gnd"]:
            power_pins = self.row_decoder_inst.get_pins(pin_name)
            power_pins = [x for x in power_pins if x.layer == METAL3]
            for power_pin in power_pins:
                m2_m3_blockages.append((power_pin.by(), power_pin.uy()))

        if self.num_banks == 2 and self.column_decoder_inst is not None:
            # prevent select pins clash
            sel_rails_height = (1 + self.words_per_row) * self.bus_pitch
            m2_m3_blockages.append((self.left_col_mux_select_y,
                                    self.left_col_mux_select_y + sel_rails_height))

        if self.num_banks == 2:
            # prevent clashes with wl output to left bank
            decoder_out_offsets = self.get_decoder_output_offsets(self.bank_insts[-1])
            for y_offset in decoder_out_offsets:
                m2_m3_blockages.append((y_offset, y_offset + self.m3_width))

        m2_m3_blockages = list(sorted(m2_m3_blockages, key=lambda x: x[0]))
        via_top = self.mid_vdd.height - via_pitch
        via_offsets = []

        lowest_flop_inst = min(self.bank.control_flop_insts, key=lambda x: x[2].by())[2]
        y_offset = lowest_flop_inst.by()

        while y_offset < via_top:
            if len(m2_m3_blockages) > 0 and m2_m3_blockages[0][0] <= y_offset + via_pitch:
                y_offset = m2_m3_blockages[0][1] + wide_space
                m2_m3_blockages.pop(0)
            else:
                via_offsets.append(y_offset)
                y_offset += via_pitch
        return via_offsets, fill_height

    def add_left_power_rail_vias(self, via_offsets, rail_top, fill_height):
        m4_power_pins = self.right_bank_inst.get_pins("vdd") + self.right_bank_inst.get_pins("gnd")
        if self.num_banks == 2:
            m4_power_pins.extend(self.left_bank_inst.get_pins("vdd") + self.left_bank_inst.get_pins("gnd"))
        m4_power_pins = [x for x in m4_power_pins if x.layer == METAL4]

        self.m4_vdd_rects = []
        self.m4_gnd_rects = []
        rails = [self.mid_vdd, self.mid_gnd]
        fill_width = self.mid_vdd.width

        for i in range(2):
            rail = rails[i]
            for y_offset in via_offsets:
                via_offset = vector(rail.cx(), y_offset + 0.5 * fill_height)
                self.add_contact_center(m2m3.layer_stack, offset=via_offset,
                                        size=[1, 2], rotate=90)
                self.add_contact_center(m3m4.layer_stack, offset=via_offset,
                                        size=[1, 2], rotate=90)
                self.add_rect_center(METAL3, offset=via_offset, width=fill_width,
                                     height=fill_height)

            rect = self.add_rect(METAL4, offset=rail.ll(),
                                 width=rail.width, height=rail_top - rail.by())
            if i % 2 == 0:
                self.m4_vdd_rects.append(rect)
            else:
                self.m4_gnd_rects.append(rect)

        self.m4_power_pins = m4_power_pins

    def route_col_decoder_power(self):
        rails = [self.mid_vdd, self.mid_gnd]
        pin_names = ["vdd", "gnd"]
        for i in range(2):
            pin_name = pin_names[i]
            if self.words_per_row == 2:
                y_shift = self.column_decoder_inst.by() + self.column_decoder.flop_inst.by()
                x_shift = self.column_decoder_inst.lx() + self.column_decoder.flop_inst.lx()
                pins = self.column_decoder.flop.get_pins(pin_name)

                for pin in pins:
                    via = m2m3 if pin.layer == METAL3 else m1m2
                    pin_y = pin.by() + y_shift
                    self.add_rect(pin.layer, offset=vector(rails[i].lx(), pin_y),
                                  height=pin.height(), width=pin.lx() + x_shift - rails[i].lx())
                    self.add_contact_center(via.layer_stack,
                                            offset=vector(rails[i].cx(), pin_y + 0.5 * pin.height()),
                                            size=[1, 2], rotate=90)
            else:

                for pin in self.column_decoder_inst.get_pins(pin_name):
                    self.route_predecoder_col_mux_power_pin(pin, rails[i])

    def route_predecoder_col_mux_power_pin(self, pin, rail):
        via = m1m2 if pin.layer == METAL1 else m2m3
        self.add_rect(pin.layer, offset=vector(rail.lx(), pin.by()),
                      width=pin.lx() - rail.lx(), height=pin.height())
        self.add_contact_center(via.layer_stack, offset=vector(rail.cx(), pin.cy()),
                                size=[1, 2], rotate=90)

    def route_left_bank_power(self):
        if self.num_banks == 1:
            return
        debug.info(1, "Route left bank sram power")
        rails = [self.mid_gnd, self.mid_vdd]
        pin_names = ["gnd", "vdd"]
        for i in range(2):
            rail = rails[i]
            for pin in self.left_bank.wordline_driver_inst.get_pins(pin_names[i]):
                pin_x = self.left_bank_inst.rx() - pin.lx()
                y_offset = self.left_bank_inst.by() + pin.by()
                self.add_rect(pin.layer, offset=vector(pin_x, y_offset), height=pin.height(),
                              width=rail.rx() - pin_x)
                if pin.layer == METAL3:
                    y_offset = pin.cy() + self.left_bank_inst.by()
                    self.add_contact_center(m2m3.layer_stack, offset=vector(rail.cx(), y_offset),
                                            size=[1, 2], rotate=90)

    def get_decoder_output_offsets(self, bank_inst):
        offsets = []

        buffer_mod = self.bank.wordline_driver.logic_buffer
        gnd_pin = buffer_mod.get_pin("gnd")

        odd_rail_y = gnd_pin.uy() + self.get_parallel_space(METAL3)
        even_rail_y = buffer_mod.height - odd_rail_y - self.m3_width

        for row in range(self.bank.num_rows):
            if row % 2 == 0:
                rail_y = even_rail_y
            else:
                rail_y = odd_rail_y
            y_shift = self.bank.wordline_driver_inst.mod.bitcell_offsets[row]
            offsets.append(y_shift + rail_y + bank_inst.mod.wordline_driver_inst.by())

        return offsets

    def route_decoder_outputs(self):
        # place m3 rail to the bank wordline drivers just below the power rail

        fill_height = m2m3.height
        _, fill_width = self.calculate_min_area_fill(fill_height, layer=METAL2)

        y_offsets = self.get_decoder_output_offsets(self.bank_insts[0])

        for row in range(self.num_rows):
            decoder_out = self.row_decoder_inst.get_pin("decode[{}]".format(row))
            wl_ins = [self.right_bank_inst.get_pin("dec_out[{}]".format(row))]
            if not self.single_bank:
                wl_ins.append(self.left_bank_inst.get_pin("dec_out[{}]".format(row)))

            if row % 2 == 0:
                via_y = decoder_out.uy() - 0.5 * m2m3.second_layer_height
            else:
                via_y = decoder_out.by() - 0.5 * m2m3.second_layer_height

            via_offset = vector(decoder_out.cx() - 0.5 * self.m3_width, via_y)
            self.add_contact(m2m3.layer_stack, offset=via_offset)

            y_offset = y_offsets[row]
            self.add_rect(METAL3, offset=via_offset, height=y_offset - via_offset.y)
            if self.num_banks == 1:
                x_offset = via_offset.x
            else:
                x_offset = wl_ins[1].cx() - 0.5 * self.m3_width
            self.add_rect(METAL3, offset=vector(x_offset, y_offset),
                          width=wl_ins[0].cx() + 0.5 * self.m3_width - x_offset)

            for i in range(len(wl_ins)):
                wl_in = wl_ins[i]
                x_offset = wl_in.cx() - 0.5 * self.m3_width
                self.add_rect(METAL3, offset=vector(x_offset, wl_in.cy()),
                              height=y_offset - wl_in.cy())
                self.add_contact_center(m2m3.layer_stack, wl_in.center())
                self.add_contact_center(m1m2.layer_stack, wl_in.center())
                if fill_width > 0:
                    self.add_rect_center(METAL2, offset=wl_in.center(), width=fill_width,
                                         height=fill_height)

    def join_control(self, pin_name, y_offset):
        via_extension = 0.5 * (cross_m2m3.height - cross_m2m3.contact_width)
        left_pin = self.bank_insts[1].get_pin(pin_name)
        right_pin = self.bank_insts[0].get_pin(pin_name)
        for pin in [left_pin, right_pin]:
            self.add_cross_contact_center(cross_m2m3,
                                          offset=vector(pin.cx(),
                                                        y_offset + 0.5 * self.bus_width))
            rail_bottom = y_offset + 0.5 * self.bus_width - 0.5 * cross_m2m3.height
            if rail_bottom < pin.by():
                self.add_rect(pin.layer, offset=vector(pin.lx(), rail_bottom),
                              width=pin.width(), height=pin.by() - rail_bottom)
        join_rail = self.add_rect(METAL3,
                                  offset=vector(left_pin.lx() - via_extension,
                                                y_offset),
                                  height=self.bus_width,
                                  width=right_pin.rx() - left_pin.lx() +
                                        2 * via_extension)
        setattr(self, pin_name + "_rail", join_rail)

    def join_bank_controls(self):
        control_inputs = self.control_inputs
        if self.single_bank:
            return

        # find y offset of connecting rails
        cross_clk_rail_y = self.bank.cross_clk_rail.offset.y
        if self.num_banks == 2:
            cross_clk_rail_y = min(cross_clk_rail_y,
                                   self.left_bank_inst.by() + self.left_bank.cross_clk_rail.offset.y)

        if self.column_decoder_inst is not None:
            vdd_pin = min(self.column_decoder_inst.get_pins("vdd"), key=lambda x: x.by())
            cross_clk_rail_y = min(cross_clk_rail_y,
                                   vdd_pin.by() - self.get_parallel_space(METAL3))

        y_offset = (cross_clk_rail_y - (len(control_inputs) * self.bus_pitch))

        for i in range(len(control_inputs)):
            self.join_control(control_inputs[i], y_offset)
            y_offset += self.bus_pitch

    def fill_decoder_wordline_space(self):
        wordline_logic = self.bank.wordline_driver.logic_buffer.logic_mod
        decoder_inverter = self.row_decoder.inv_inst[-1].mod
        fill_layers, fill_purposes = [], []
        for layer, purpose in zip(*get_default_fill_layers()):
            if layer not in [ACTIVE]:
                # No NIMP, PWELL to prevent min spacing to PIMP, NWELL respectively
                fill_layers.append(layer)
                fill_purposes.append(purpose)
        rects = create_wells_and_implants_fills(decoder_inverter,
                                                wordline_logic, layers=fill_layers,
                                                purposes=fill_purposes)
        x_offset = self.row_decoder_inst.lx() + self.row_decoder.inv_inst[-1].rx()
        width = (self.right_bank_inst.lx() + self.bank.wordline_driver_inst.lx() +
                 self.bank.wordline_driver.buffer_insts[0].lx()) - x_offset
        bitcell_height = self.bitcell.height
        mod_height = wordline_logic.height

        bitcell_rows_per_driver = round(wordline_logic.height / bitcell_height)

        for row in range(0, self.num_rows, bitcell_rows_per_driver):
            y_base = (self.bank.bitcell_array_inst.by() + self.bank_inst.by() +
                      self.row_decoder.bitcell_offsets[row])
            for layer, rect_bottom, rect_top, left_rect, right_rect in rects:
                if ((left_rect.height >= mod_height or right_rect.height >= mod_height) and
                        layer in [PIMP, NIMP]):
                    # prevent overlap between NIMP and PIMP spanning entire logic
                    continue
                if right_rect.uy() > mod_height or left_rect.uy() > mod_height:
                    rect_top = max(right_rect.uy(), left_rect.uy())
                if right_rect.by() < 0 or left_rect.by() < 0:
                    rect_bottom = min(right_rect.by(), left_rect.by())
                if layer in [NIMP, PWELL]:
                    # prevent space from pimplant to nimplant or PWELL to NWELL
                    rect_top = max(left_rect.uy(), right_rect.uy())
                # cover align with bitcell nwell
                if row % (2 * bitcell_rows_per_driver) == 0:
                    y_offset = y_base + (mod_height - rect_top)
                else:
                    y_offset = y_base + rect_bottom
                self.add_rect(layer, offset=vector(x_offset, y_offset), width=width,
                              height=rect_top - rect_bottom)
        # join wells from row decoder to left bank
        if self.num_banks == 1:
            return

        for row in range(0, self.num_rows + 1, bitcell_rows_per_driver):
            if row % (2 * bitcell_rows_per_driver) == 0:
                well = "nwell"
            else:
                well = "pwell"
            well_height = getattr(self.row_decoder, f"contact_{well}_height", None)
            if well_height:
                driver_inst = self.bank_insts[-1].mod.wordline_driver_inst
                right_x = self.row_decoder.contact_mid_x + self.row_decoder_inst.lx()
                buffer_x = driver_inst.mod.buffer_insts[0].lx() + well_height  # extra well_height
                start_x = (self.bank_insts[1].rx() -
                           self.bank_insts[-1].mod.wordline_driver_inst.lx() - buffer_x)
                if row == self.num_rows:
                    y_base = self.row_decoder.bitcell_offsets[row - 1] + bitcell_height
                else:
                    y_base = self.row_decoder.bitcell_offsets[row]

                y_offset = self.bank.bitcell_array_inst.by() + y_base - 0.5 * well_height
                self.add_rect(well, vector(start_x, y_offset),
                              width=right_x - start_x,
                              height=well_height)

    def get_power_grid_forbidden_regions(self):
        return [], []

    def route_power_grid(self):
        if not self.add_power_grid:
            for i in range(self.num_banks):
                self.copy_layout_pin(self.bank_insts[i], "vdd")
                self.copy_layout_pin(self.bank_insts[i], "gnd")
            return

        debug.info(1, "Route sram power grid")

        second_top_layer, top_layer = tech.power_grid_layers
        debug.check(int(second_top_layer[5:]) > 4 and int(top_layer[5:]) > 5,
                    "Power grid only supported for > M4")

        power_grid_width = getattr(tech, "power_grid_width", self.m4_width)
        power_grid_x_space = getattr(tech, "power_grid_x_space",
                                     self.get_wide_space(second_top_layer))
        power_grid_y_space = getattr(tech, "power_grid_y_space",
                                     self.get_wide_space(top_layer))

        # second_top to top layer via
        m_top_via = ContactFullStack(start_layer=second_top_layer, stop_layer=top_layer,
                                     centralize=True, max_width=power_grid_width)
        # m4 to second_top layer vias
        all_m4_power_pins = self.m4_power_pins + self.m4_gnd_rects + self.m4_vdd_rects
        all_m4_power_widths = list(set([utils.round_to_grid(x.rx() - x.lx())
                                        for x in all_m4_power_pins]))

        if not second_top_layer == METAL5:
            m5_via = ContactFullStack(start_layer=METAL5, stop_layer=second_top_layer,
                                      centralize=True, max_width=m_top_via.width)
            via_m5_height = m5_via.via_insts[0].mod.first_layer_width
        else:
            m5_via = None
            via_m5_height = None

        self.power_grid_m4_vias = all_m4_vias = {}

        for width in all_m4_power_widths:
            via_height = max(power_grid_width, via_m5_height or 0.0)
            all_m4_vias[width] = ContactFullStack(start_layer=METAL4, stop_layer=METAL5,
                                                  centralize=True, max_width=width,
                                                  max_height=via_height)

        # dimensions of vertical top layer grid
        left = min(map(lambda x: x.cx(), all_m4_power_pins)) - 0.5 * m_top_via.width
        right = max(map(lambda x: x.cx(), all_m4_power_pins)) - 0.5 * m_top_via.width
        bottom = min(map(lambda x: x.by(), all_m4_power_pins))
        top = max(map(lambda x: x.uy(), all_m4_power_pins)) - m_top_via.height

        x_forbidden, y_forbidden = self.get_power_grid_forbidden_regions()

        # add top layer
        top_layer_width = m_top_via.width
        top_layer_space = max(power_grid_x_space, self.get_wide_space(top_layer))
        top_layer_pitch = top_layer_width + top_layer_space
        top_layer_pins = []

        x_offset = left
        i = 0
        while x_offset < right:
            pin_name = "gnd" if i % 2 == 0 else "vdd"
            top_layer_pins.append(self.add_layout_pin(pin_name, top_layer, offset=vector(x_offset, bottom),
                                                      width=top_layer_width, height=top - bottom))
            while True:  # skip forbiddens
                x_offset += top_layer_pitch
                is_collision = False
                for (left_, right_) in x_forbidden:
                    if left_ <= x_offset <= right_:
                        is_collision = True
                        break
                if not is_collision:
                    break
            i += 1
        top_gnd = top_layer_pins[0::2]
        top_vdd = top_layer_pins[1::2]

        # add second_top layer
        y_offset = bottom
        rail_height = max(map(lambda x: x.height, [m_top_via] + list(all_m4_vias.values())))
        rail_space = max(power_grid_y_space, self.get_wide_space(second_top_layer))

        m4_space = self.get_wide_space(METAL4)

        rail_pitch = rail_height + rail_space

        m4_vdd_rects = self.m4_vdd_rects + [x for x in self.m4_power_pins if x.name == "vdd"]
        self.m4_vdd_rects = m4_vdd_rects = list(sorted(m4_vdd_rects, key=lambda x: x.lx()))
        m4_gnd_rects = self.m4_gnd_rects + [x for x in self.m4_power_pins if x.name == "gnd"]
        m4_gnd_rects = list(sorted(m4_gnd_rects, key=lambda x: x.lx()))

        i = 0
        while y_offset < top - m_top_via.height:
            rail_rect = self.add_rect(second_top_layer, offset=vector(left, y_offset),
                                      height=rail_height,
                                      width=right + m_top_via.width - left)
            # connect to top grid
            top_pins = top_gnd if i % 2 == 0 else top_vdd
            for top_pin in top_pins:
                self.add_inst(m_top_via.name, m_top_via,
                              offset=vector(top_pin.cx(), rail_rect.cy() - 0.5 * m_top_via.height))
                self.connect_inst([])

            # connect to m4 below
            m4_rects = m4_gnd_rects if i % 2 == 0 else m4_vdd_rects

            prev_m4_rect = None

            for m4_rect in m4_rects:
                if m4_rect.by() < y_offset and m4_rect.uy() > rail_rect.uy():
                    m4_rect_width = utils.round_to_grid(m4_rect.rx() - m4_rect.lx())
                    m4_via = all_m4_vias[m4_rect_width]

                    if prev_m4_rect:
                        if (m4_rect.cx() - 0.5 * m4_via.width <
                                prev_m4_rect.cx() + 0.5 * m4_via.width + m4_space):
                            continue
                    # add m4 via
                    self.add_inst(m4_via.name, mod=m4_via,
                                  offset=vector(m4_rect.cx(), rail_rect.cy() - 0.5 * m4_via.height))
                    self.connect_inst([])
                    # add m4 via
                    if m5_via:
                        m4_m4_via = m4_via.via_insts[0].mod
                        if prev_m4_rect and (m4_rect.cx() - 0.5 * m5_via.width <
                                             prev_m4_rect.cx() + 0.5 * m5_via.width + rail_space):
                            # just connect using M5
                            rect_height = m4_m4_via.second_layer_width
                            self.add_rect(METAL5, offset=vector(prev_m4_rect.cx(),
                                                                rail_rect.cy() - 0.5 * rect_height),
                                          width=m4_rect.cx() + 0.5 * m4_via.width -
                                                prev_m4_rect.cx(),
                                          height=rect_height)
                        else:
                            self.add_inst(m5_via.name, mod=m5_via,
                                          offset=vector(m4_rect.cx(),
                                                        rail_rect.cy() - 0.5 * m5_via.height))
                            self.connect_inst([])

                    prev_m4_rect = m4_rect

            while True:  # skip forbiddens
                y_offset += rail_pitch
                is_collision = False
                for (bottom_, top_) in y_forbidden:
                    if bottom_ <= y_offset <= top_:
                        is_collision = True
                        break
                if not is_collision:
                    break
            i += 1

    def add_lvs_correspondence_points(self):
        pass

    def add_cross_contact_center(self, cont, offset, rotate=False,
                                 rail_width=None, fill=True):
        cont_inst = super().add_cross_contact_center(cont, offset, rotate)
        if fill:
            self.add_cross_contact_center_fill(cont, offset, rotate, rail_width)
        return cont_inst
