import math
from types import SimpleNamespace

import debug
from base import contact
from base import design
from base import utils
from base.contact import m1m2, active as active_contact
from base.design import METAL1, PO_DUMMY, PIMP, NIMP, NWELL, METAL2, POLY, PWELL, TAP_ACTIVE
from base.utils import round_to_grid
from base.vector import vector
from base.well_active_contacts import calculate_contact_width
from base.well_implant_fills import calculate_tx_metal_fill
from globals import OPTS
from pgates.pgates_characterization_base import pgates_characterization_base
from pgates.ptx_spice import ptx_spice
from tech import drc, parameter, info
from tech import layer as tech_layers


class pgate(pgates_characterization_base, design.design):
    """
    This is a module that implements some shared functions for parameterized gates.
    """

    # c = __import__(OPTS.bitcell)
    # bitcell = getattr(c, OPTS.bitcell)()
    bitcell = SimpleNamespace(height=3)
    num_tracks = 1

    @classmethod
    def get_class_name(cls):
        raise NotImplementedError

    @classmethod
    def get_name(cls, size=1, beta=None, height=None,
                 contact_pwell=True, contact_nwell=True, align_bitcell=False, same_line_inputs=False,
                 *args, **kwargs):
        name = "{}_{:.3g}".format(cls.get_class_name(), size)
        if beta is None:
            beta = parameter["beta"]

        if not contact_pwell:
            name += "_no_p"
        if not contact_nwell:
            name += "_no_n"
        if not beta == parameter["beta"]:
            name += "_b" + str(beta)
        height, height_suffix = cls.get_height(height, align_bitcell)
        name += height_suffix
        if align_bitcell:
            name += "_align"
        if same_line_inputs:
            name += "_same_line"
        name = name.replace(".", "__")
        # TODO: sky_tapeout: prevent hack clash
        if name in cls._cache and getattr(OPTS, "begin_adjacent_pgate_hack", False):
            name += "_hack"

        return name

    def __init__(self, name, height, size=1, beta=None, contact_pwell=True, contact_nwell=True,
                 align_bitcell=False, same_line_inputs=False):
        """ Creates a generic cell """
        design.design.__init__(self, name)
        height, height_suffix = self.get_height(height, align_bitcell)

        if beta is None:
            beta = parameter["beta"]
        self.beta = beta
        self.size = size
        self.contact_pwell = contact_pwell
        self.contact_nwell = contact_nwell
        self.height = height
        self.align_bitcell = align_bitcell
        if self.align_bitcell and OPTS.use_x_body_taps:
            self.contact_pwell = self.contact_nwell = False
        self.same_line_inputs = same_line_inputs
        self.implant_to_channel = drc["implant_to_channel"]

    @staticmethod
    def get_height(height, align_bitcell):
        if hasattr(OPTS, "logic_buffers_height"):
            default_height = OPTS.logic_buffers_height
        else:
            default_height = pgate.bitcell.height

        if align_bitcell and height is None:
            height = pgate.bitcell.height
        elif height is None:
            height = default_height

        if not align_bitcell and not height == default_height:
            height_suffix = "_h_" + "{:.5g}".format(height)
        else:
            height_suffix = ""
        return height, height_suffix

    def setup_drc_constants(self):
        super().setup_drc_constants()
        pitch_res = self.calculate_poly_pitch()
        self.ptx_poly_space, self.ptx_poly_width, self.ptx_poly_pitch = pitch_res

    def shrink_if_needed(self):
        # TODO tune this based on bitcell height. Reduce factors if bitcell too short
        _nmos_scale = self.nmos_scale
        _pmos_scale = self.pmos_scale
        shrink_factors = [1, 0.9, 0.8, 0.7, 0.6]
        if "nand" in self.__class__.__name__:
            # higher beta can help nand's since pmos wouldn't reach min_width as quickly
            beta_factors = [1, 1.2, 1.4]
        elif "nor" in self.__class__.__name__:
            beta_factors = [1, 1 / 1.25, 1 / 1.5, 1 / 1.75]
        else:
            beta_factors = [1]
        original_beta = self.beta

        exit_beta_loop = False
        for beta_factor in beta_factors:
            self.beta = original_beta * beta_factor
            for shrink_factor in shrink_factors:
                self.pmos_scale = _pmos_scale * shrink_factor
                self.nmos_scale = _nmos_scale * shrink_factor
                if self.determine_tx_mults():
                    exit_beta_loop = True
                    break
            if exit_beta_loop:
                break

    def get_total_vertical_space(self, nmos_width=None, pmos_width=None):
        """Estimate space below nfets, between nfets and pfets and above pfets"""
        if nmos_width is None:
            nmos_width = utils.round_to_grid(self.nmos_size * self.min_tx_width)
        if pmos_width is None:
            pmos_width = utils.round_to_grid(self.pmos_size * self.min_tx_width)

        # top and bottom space based on power rails
        self.top_space, self.pmos_fill_width, self.pmos_fill_height = \
            self.get_vertical_space(pmos_width)
        self.bottom_space, self.nmos_fill_width, self.nmos_fill_height = \
            self.get_vertical_space(nmos_width)

        # top and bottom space based on poly
        poly_poly_allowance = self.poly_extend_active + 0.5 * (self.poly_vert_space or self.poly_space)
        self.top_space = max(self.top_space, poly_poly_allowance)
        self.bottom_space = max(self.bottom_space, poly_poly_allowance)

        # estimate top and bottom space based on well contacts
        self.well_contact_active_height = contact.well.first_layer_width
        self.well_contact_implant_height = max(self.implant_width,
                                               self.well_contact_active_height +
                                               2 * self.implant_enclose_active)

        poly_to_active = max(drc.get("poly_dummy_to_vert_active", self.poly_to_active), self.poly_to_active)
        active_contact_space = self.poly_extend_active + poly_to_active + 0.5 * self.well_contact_active_height
        implant_active_space = self.implant_enclose_ptx_active + 0.5 * self.well_contact_implant_height
        # estimate based on space from fet active to tap active
        active_active_space = self.get_space("active") + 0.5 * self.well_contact_active_height

        if self.contact_nwell:
            self.top_space = max(self.top_space, active_contact_space, implant_active_space,
                                 active_active_space)
        if self.contact_pwell:
            self.bottom_space = max(self.bottom_space, active_contact_space, implant_active_space,
                                    active_active_space)

        # middle space
        # first calculate height of tracks (all poly contacts)
        line_space = max(self.get_line_end_space(METAL1), self.get_line_end_space(METAL2))

        if self.same_line_inputs:
            self.gate_rail_pitch = (0.5 * contact.m1m2.second_layer_height +
                                    line_space + 0.5 * self.m2_width)
            max_fill_width = 2 * self.ptx_poly_pitch - 2 * self.m1_space - contact.poly.second_layer_width
            _, fill_height = self.calculate_min_area_fill(max_fill_width, layer=METAL1)
            if fill_height == 0:
                fill_width = max(contact.poly.second_layer_width, contact.m1m2.first_layer_width)
                fill_height = contact.poly.second_layer_height
            else:
                fill_height, fill_width = self.calculate_min_area_fill(fill_height, min_height=self.m1_width,
                                                                       layer=METAL1)

            track_extent = max(fill_height, contact.poly.second_layer_height,
                               contact.m1m2.second_layer_height)
        else:
            # TODO: sky_tapeout: fix gate pin pitch
            if getattr(OPTS, "begin_adjacent_pgate_hack", False):
                width = m1m2.w_2
            else:
                width = self.m2_width

            self.gate_rail_pitch = (0.5 * width +
                                    line_space + 0.5 * width)
            track_extent = (self.num_tracks - 1) * self.gate_rail_pitch
            track_extent += max(contact.poly.second_layer_height, contact.m1m2.second_layer_height)

            fill_height = track_extent
            fill_width = max(contact.poly.second_layer_width, contact.m1m2.first_layer_width)

        self.gate_fill_height = fill_height
        self.gate_fill_width = fill_width
        self.mid_track_extent = track_extent

        # mid space assuming overlap with active
        active_to_poly_contact = drc.get("poly_contact_to_n_active", drc["poly_contact_to_active"])
        poly_contact_base = (active_to_poly_contact + 0.5 * self.contact_width -
                             0.5 * contact.poly.second_layer_height)
        self.track_bot_space = max(poly_contact_base,
                                   (max(0, 0.5 * self.nmos_fill_height - 0.5 * nmos_width) +
                                    self.line_end_space))
        self.track_bot_space = utils.ceil(self.track_bot_space)

        active_to_poly_contact = drc.get("poly_contact_to_p_active", drc["poly_contact_to_active"])
        p_poly_contact_base = (active_to_poly_contact + 0.5 * self.contact_width -
                               0.5 * contact.poly.second_layer_height)
        self.track_top_space = max(p_poly_contact_base,
                                   (max(0, 0.5 * self.pmos_fill_height - 0.5 * pmos_width) +
                                    self.line_end_space))
        self.track_top_space = utils.ceil(self.track_top_space)
        self.middle_space = max(self.track_bot_space + track_extent + self.track_top_space,
                                2 * self.implant_to_channel)

        return self.top_space + self.middle_space + self.bottom_space

    def get_vertical_space(self, tx_width_):
        """Get space above or below transistor from active top/bottom to power rail"""
        _, _, fill_width, fill_height = calculate_tx_metal_fill(tx_width_, self,
                                                                contact_if_none=True)
        fill_height = max(fill_height, m1m2.first_layer_height)
        max_width = max(self.rail_height, fill_height)
        min_width = min(self.rail_height, fill_height)
        power_rail_space = self.get_space_by_width_and_length(METAL1,
                                                              max_width=max_width,
                                                              min_width=min_width,
                                                              run_length=fill_width,
                                                              heights=[fill_width, fill_width])
        via_extension = max(0, 0.5 * (m1m2.height - tx_width_))
        rail_extent = 0.5 * self.rail_height + power_rail_space + via_extension
        return rail_extent, fill_width, fill_height

    def determine_tx_mults(self):
        """
        Determines the number of fingers needed to achieve the size within
        the height constraint. This may fail if the user has a tight height.
        """

        self.nmos_size = self.nmos_scale * self.size
        self.pmos_size = self.beta * self.pmos_scale * self.size

        self.tx_height_available = tx_height_available = self.height - self.get_total_vertical_space()

        min_n_width = utils.round_to_grid(self.nmos_scale * self.min_tx_width)
        min_p_width = utils.round_to_grid(self.pmos_scale * self.beta * self.min_tx_width)
        if min_n_width < self.min_tx_width or min_p_width < self.min_tx_width:
            return False

        if tx_height_available < min_n_width + min_p_width:
            debug.info(2, "Warning: Cell height {0} too small for simple pmos height {1}, nmos height {2}.".format(
                self.height, min_p_width, min_n_width))
            return False

        # Determine the number of mults for each to fit width into available space
        self.nmos_width = utils.round_to_grid(self.nmos_size * self.min_tx_width)
        self.pmos_width = utils.round_to_grid(self.pmos_size * self.min_tx_width)
        # Divide the height according to size ratio
        nmos_height_available = self.nmos_width / (self.nmos_width + self.pmos_width) * tx_height_available
        pmos_height_available = self.pmos_width / (self.nmos_width + self.pmos_width) * tx_height_available

        debug.info(2, "Height avail {0} PMOS height {1} NMOS height {2}".format(
            tx_height_available, pmos_height_available, nmos_height_available))

        nmos_required_mults = max(int(math.ceil(self.nmos_width / nmos_height_available)), 1)
        pmos_required_mults = max(int(math.ceil(self.pmos_width / pmos_height_available)), 1)
        # The mults must be the same for easy connection of poly
        self.tx_mults = max(nmos_required_mults, pmos_required_mults)

        # Recompute each mult width and check it isn't too small
        # This could happen if the height is narrow and the size is small
        # User should pick a bigger size to fix it...
        # We also need to round the width to the grid or we will end up with LVS property
        # mismatch errors when fingers are not a grid length and get rounded in the offset geometry.
        self.nmos_width = round_to_grid(self.nmos_width / self.tx_mults)
        debug.check(self.nmos_width >= self.min_tx_width,
                    "{}: Cannot finger NMOS transistors to fit cell height.".format(self.name))
        self.pmos_width = round_to_grid(self.pmos_width / self.tx_mults)
        debug.check(self.pmos_width >= self.min_tx_width,
                    "{}: Cannot finger PMOS transistors to fit cell height.".format(self.name))

        return True

    def get_output_x(self):
        active_right = self.active_mid_x + 0.5 * self.active_width
        right_most_contact_mid = (active_right - self.end_to_poly + 0.5 * self.ptx_poly_pitch -
                                  0.5 * self.ptx_poly_width)

        fill_width = max(self.nmos_fill_width, self.pmos_fill_width)
        fill_right = right_most_contact_mid + 0.5 * fill_width
        return max(self.get_parallel_space(METAL1) + fill_right,
                   self.get_space("via1") + 0.5 * m1m2.first_layer_width +
                   right_most_contact_mid)

    def setup_layout_constants(self):

        self.contact_pitch = self.ptx_poly_pitch

        self.get_total_vertical_space(self.nmos_width, self.pmos_width)

        non_active_height = self.height - (self.nmos_width + self.pmos_width)
        # unaccounted for space
        extra_space = non_active_height - (self.top_space + self.bottom_space + self.middle_space)

        # redistribute spaces
        additional_space = max(utils.floor(0.33 * extra_space), 0)
        actual_bottom_space = self.bottom_space + additional_space
        actual_top_space = self.top_space + additional_space
        self.track_bot_space = self.track_bot_space + utils.floor(0.5 * additional_space)

        self.active_mid_y_pmos = self.height - actual_top_space - 0.5 * self.pmos_width
        self.active_mid_y_nmos = actual_bottom_space + 0.5 * self.nmos_width

        nmos_active_top = self.active_mid_y_nmos + 0.5 * self.nmos_width
        self.mid_y = nmos_active_top + self.track_bot_space + 0.5 * self.mid_track_extent
        self.mid_y = utils.round_to_grid(self.mid_y)

        nwell_active_space = drc.get("nwell_to_active_space", 0)
        self.nwell_y = max(self.mid_y, nmos_active_top + nwell_active_space)

        self.active_enclose_contact = max(drc["active_enclosure_contact"],
                                          (self.active_width - self.contact_width) / 2)

        self.end_to_poly = self.active_enclose_contact + self.contact_width + self.contact_to_gate

        self.active_width = 2 * self.end_to_poly + self.tx_mults * self.ptx_poly_pitch - self.ptx_poly_space

        # recalculate middle space
        self.middle_space = non_active_height - (actual_top_space + actual_bottom_space)

        if PO_DUMMY in tech_layers:
            self.num_dummy_poly = 2 * self.num_poly_dummies
            self.total_poly = self.tx_mults + self.num_dummy_poly
            poly_extent = self.total_poly * self.ptx_poly_pitch - self.ptx_poly_space
            self.width = poly_extent - self.ptx_poly_width
            self.active_mid_x = 0.5 * self.width
            self.poly_x_start = 0.0
        else:
            self.num_dummy_poly = 0
            self.total_poly = self.tx_mults + self.num_dummy_poly

            # TODO: sky_tapeout: nwell extending into adjacent cells is problematic
            if getattr(OPTS, "begin_adjacent_pgate_hack", False):
                implant_enclose_ptx_active = 0.34
            else:
                implant_enclose_ptx_active = self.implant_enclose_ptx_active
            self.active_mid_x = self.active_width / 2 + implant_enclose_ptx_active

            output_x = self.get_output_x()
            self.width = output_x + self.m1_width + self.get_parallel_space(METAL1)

            self.poly_x_start = (self.active_mid_x - 0.5 * self.active_width +
                                 self.end_to_poly + 0.5 * self.ptx_poly_width)

        # nwell
        # TODO: sky_tapeout: nwell extending into adjacent cells is problematic
        well_space = 1.27
        self.width = max(self.width, well_space)

        self.implant_width = max(self.width, self.active_width + 2 * self.implant_enclose_ptx_active)
        self.mid_x = self.width / 2

        self.poly_height = (self.middle_space + self.nmos_width + self.pmos_width +
                            2 * self.poly_extend_active)

        self.calculate_body_contacts()

        self.nimplant_height = self.nwell_y - 0.5 * self.well_contact_implant_height
        self.pimplant_height = self.height - 0.5 * self.well_contact_implant_height - self.nwell_y

        self.nwell_height = (self.height - self.nwell_y + 0.5 * self.well_contact_active_height +
                             self.well_enclose_active)
        self.nwell_width = max(self.implant_width, self.active_width + 2 * self.well_enclose_ptx_active,
                               self.nwell_width)

        if info["has_pwell"]:  # prevent overlap with adjacent cell's pwell
            self.nwell_width = self.width

        self.pmos_contacts = self.calculate_num_contacts(self.pmos_width)
        self.nmos_contacts = self.calculate_num_contacts(self.nmos_width)
        self.active_contact_layers = active_contact.layer_stack

    def calculate_poly_pitch(self):
        num_independent_contacts = 2 if self.num_tracks > 2 else 1
        # TODO fix two input case
        return ptx_spice.calculate_poly_pitch(self, num_independent_contacts)

    def calculate_body_contacts(self):

        active_width, body_contact = calculate_contact_width(self, self.width,
                                                             self.well_contact_active_height)
        self.body_contact = body_contact

        self.well_contact_active_width = active_width

        implant_area = self.get_min_area(PIMP) or 0
        self.contact_implant_width = max(self.implant_width,
                                         utils.ceil(self.well_contact_active_width +
                                                    2 * self.implant_enclose_active),
                                         utils.ceil_2x_grid(implant_area /
                                                            self.well_contact_implant_height))

        self.nwell_width = self.contact_nwell_width = max(self.contact_implant_width,
                                                          self.well_contact_active_width +
                                                          2 * self.well_enclose_active)
        self.contact_nwell_height = body_contact.first_layer_width + 2 * self.well_enclose_active

    def add_poly(self):
        poly_offsets = []
        half_dummy = int(0.5 * self.num_dummy_poly)
        poly_layers = half_dummy * [PO_DUMMY] + self.tx_mults * [POLY] + half_dummy * [PO_DUMMY]

        poly_y_offset = self.active_mid_y_nmos - 0.5 * self.nmos_width - self.poly_extend_active
        self.poly_rects = []

        for i in range(len(poly_layers)):
            mid_offset = vector(self.poly_x_start + i * self.ptx_poly_pitch,
                                poly_y_offset + 0.5 * self.poly_height)
            poly_offsets.append(mid_offset)
            offset = mid_offset - vector(0.5 * self.ptx_poly_width,
                                         0.5 * self.poly_height)
            rect = self.add_rect(poly_layers[i], offset=offset, width=self.ptx_poly_width,
                                 height=self.poly_height)
            if poly_layers[i] == POLY:
                self.poly_rects.append(rect)
        if half_dummy > 0:
            self.poly_offsets = poly_offsets[half_dummy: -half_dummy]
        else:
            self.poly_offsets = poly_offsets

    def add_active(self):
        heights = [self.pmos_width, self.nmos_width]
        active_y_offsets = [self.active_mid_y_pmos, self.active_mid_y_nmos]
        active_rects = []
        for i in range(2):
            offset = vector(self.active_mid_x, active_y_offsets[i])
            active_rects.append(self.add_rect_center("active", offset=offset,
                                                     width=self.active_width, height=heights[i]))
        self.p_active_rect, self.n_active_rect = active_rects

    def calculate_source_drain_pos(self):
        poly_mid_to_cont_mid = 0.5 * self.ptx_poly_width + self.contact_to_gate + 0.5 * contact.active.contact_width
        contact_x_start = self.poly_offsets[0].x - poly_mid_to_cont_mid
        self.source_positions = [contact_x_start]
        self.drain_positions = []
        for i in range(self.tx_mults):
            x_offset = self.poly_offsets[i].x + poly_mid_to_cont_mid
            if i % 2 == 0:
                self.drain_positions.append(x_offset)
            else:
                self.source_positions.append(x_offset)

    def connect_to_vdd(self, positions):
        for i in range(len(positions)):
            offset = vector(positions[i] - 0.5 * self.m1_width, self.active_mid_y_pmos)
            self.add_rect("metal1", offset=offset, height=self.height - self.active_mid_y_pmos)
            offset = vector(positions[i], self.active_mid_y_pmos)
            self.add_contact_center(layers=self.active_contact_layers, offset=offset,
                                    size=(1, self.pmos_contacts))

    def connect_to_gnd(self, positions):
        for i in range(len(positions)):
            offset = vector(positions[i] - 0.5 * self.m1_width, 0)
            self.add_rect("metal1", offset=offset, height=self.active_mid_y_nmos)
            offset = vector(positions[i], self.active_mid_y_nmos)
            self.add_contact_center(layers=self.active_contact_layers, offset=offset,
                                    size=(1, self.nmos_contacts))

    def connect_positions_m2(self, positions, mid_y, tx_width, num_contacts, contact_shift):
        m1m2_layers = contact.m1m2.layer_stack
        for i in range(len(positions)):
            x_offset = positions[i]
            offset = vector(x_offset, mid_y)
            active_cont = self.add_contact_center(layers=self.active_contact_layers, offset=offset,
                                                  size=(1, num_contacts))
            m1m2_cont = self.add_contact_center(layers=m1m2_layers, offset=offset,
                                                size=(1, max(1, num_contacts - 1)))
            fill = calculate_tx_metal_fill(tx_width, self, contact_if_none=False)
            if fill:
                if mid_y < self.mid_y:
                    fill_width, fill_height = self.nmos_fill_width, self.nmos_fill_height
                    min_fill_y = max(self.bottom_space, self.n_active_rect.cy() - 0.5 * self.nmos_fill_height)
                    fill_y = min(active_cont.by(), m1m2_cont.by(), min_fill_y)
                else:
                    fill_width, fill_height = self.pmos_fill_width, self.pmos_fill_height
                    max_fill_y = min(self.height - self.top_space, self.p_active_rect.cy() +
                                     0.5 * self.pmos_fill_height)
                    fill_y = max(active_cont.uy(), m1m2_cont.uy(), max_fill_y) - fill_height
                self.add_rect(METAL1, offset=vector(offset.x - 0.5 * fill_width, fill_y),
                              width=fill_width, height=fill_height)

        output_x = self.output_x = self.get_output_x()

        self.connect_to_out_pin(positions, mid_y, contact_shift)

        return output_x

    def connect_to_out_pin(self, positions, mid_y, contact_shift):
        min_drain_x = min(positions)
        offset = vector(positions[0], mid_y - 0.5 * self.m2_width)
        self.add_rect(METAL2, offset=offset, width=self.output_x - min_drain_x,
                      height=self.m2_width)
        m1_space = self.get_line_end_space(METAL1)
        rail_allowance = 0.5 * self.rail_height + m1_space
        if mid_y > self.mid_y:
            via_y = min(mid_y + contact_shift,
                        self.height - rail_allowance - 0.5 * m1m2.height)
        else:
            via_y = max(mid_y + contact_shift,
                        rail_allowance + 0.5 * m1m2.height)

        offset = vector(self.output_x + 0.5 * contact.m1m2.first_layer_width, via_y)
        self.add_contact_center(layers=m1m2.layer_stack, offset=offset)

    def connect_s_or_d(self, pmos_positions, nmos_positions):

        self.connect_positions_m2(positions=pmos_positions, mid_y=self.active_mid_y_pmos, tx_width=self.pmos_width,
                                  num_contacts=self.pmos_contacts,
                                  contact_shift=0.0)
        self.connect_positions_m2(positions=nmos_positions, mid_y=self.active_mid_y_nmos, tx_width=self.nmos_width,
                                  num_contacts=self.nmos_contacts,
                                  contact_shift=0.0)

    def add_poly_contacts(self, pin_names, y_shifts):

        poly_cont_shifts = [0.0] * 3
        cont_shift = 0.5 * (contact.poly.w_1 - self.ptx_poly_width)
        if self.num_tracks == 2:
            poly_cont_shifts = [-cont_shift, cont_shift]
        elif self.num_tracks == 3:
            poly_cont_shifts = [-cont_shift, 0, cont_shift]

        if self.same_line_inputs:  # all contacts are centralized and x shifted for min drc fill
            y_shifts = [0.0] * len(y_shifts)
            x_shift = 0.5 * contact.poly.second_layer_width - 0.5 * self.gate_fill_width
            if len(self.poly_offsets) == 1:
                x_shifts = [0.0]
            else:
                x_shifts = [x * x_shift for x in [1, 0, -1]]
        else:
            x_shifts = [0.0] * 3

        for i in range(len(self.poly_offsets)):
            x_offset = self.poly_offsets[i].x
            offset = vector(x_offset + x_shifts[i], self.mid_y)
            self.add_rect_center(METAL1, offset=offset, width=self.gate_fill_width,
                                 height=self.gate_fill_height)

            offset = vector(x_offset + poly_cont_shifts[i], self.mid_y + y_shifts[i])
            self.add_contact_center(layers=contact.poly.layer_stack, offset=offset)
            self.add_layout_pin_center_rect(pin_names[i], METAL1, offset)

    def add_implants(self):
        # implants
        # nimplant
        poly_y_offset = self.poly_offsets[0].y - 0.5 * self.poly_height
        nimplant_y = min(self.n_active_rect.by() - self.implant_to_channel,
                         0.5 * self.well_contact_implant_height)
        if not self.contact_pwell and self.align_bitcell:
            # Vertical neighbors have same size
            # and implants can be vertically overlapped with above/below neighbor
            if self.implant_enclose_poly:
                nimplant_y = min(nimplant_y, poly_y_offset - self.implant_enclose_poly)
            nimplant_y = min(0, nimplant_y)
        self.nimplant_height = self.nwell_y - nimplant_y

        # pimplant
        pimplant_top = max(self.p_active_rect.uy() + self.implant_to_channel,
                           self.height - 0.5 * self.well_contact_implant_height)
        if not self.contact_nwell and self.align_bitcell:
            if self.implant_enclose_poly:
                pimplant_top = max(pimplant_top, poly_y_offset + self.poly_height +
                                   self.implant_enclose_poly)
            pimplant_top = max(pimplant_top, self.height)
        self.pimplant_height = pimplant_top - self.nwell_y

        implant_x = 0.5 * (self.width - self.implant_width)
        self.add_rect(NIMP, offset=vector(implant_x, nimplant_y),
                      width=self.implant_width, height=self.nimplant_height)
        self.add_rect(PIMP, offset=vector(implant_x, self.nwell_y), width=self.implant_width,
                      height=self.pimplant_height)
        # nwell
        # TODO: sky_tapeout: nwell extending into adjacent cells is problematic
        if getattr(OPTS, "begin_adjacent_pgate_hack", False):
            nwell_x = 0
            width = self.width
        else:
            nwell_x = self.mid_x - 0.5 * self.nwell_width
            width = self.nwell_width
        self.add_rect(NWELL, offset=vector(nwell_x, self.nwell_y), width=width,
                      height=self.nwell_height)
        # pwell
        if info["has_pwell"]:
            well_y = - (0.5 * self.rail_height + self.well_enclose_active)
            self.add_rect(layer="pwell", offset=vector(nwell_x, well_y),
                          width=self.nwell_width,
                          height=self.nwell_y - well_y)

    def add_body_contacts(self):

        y_offsets = [0, self.height]
        pin_names = ["gnd", "vdd"]

        if self.align_bitcell and OPTS.use_x_body_taps:
            for i in range(len(y_offsets)):
                y_offset = y_offsets[i]
                self.add_layout_pin_center_rect(pin_names[i], METAL1,
                                                offset=vector(self.mid_x, y_offset),
                                                width=self.width, height=self.rail_height)
            return

        implants = [PIMP, NIMP]

        for i in range(len(y_offsets)):
            y_offset = y_offsets[i]
            self.add_layout_pin_center_rect(pin_names[i], METAL1, offset=vector(self.mid_x, y_offset),
                                            width=self.width, height=self.rail_height)

            if (i == 0 and self.contact_pwell) or (i == 1 and self.contact_nwell):
                self.add_rect_center(implants[i], offset=vector(self.mid_x, y_offset),
                                     width=self.contact_implant_width,
                                     height=self.well_contact_implant_height)
                self.add_rect_center(TAP_ACTIVE, offset=vector(self.mid_x, y_offset),
                                     width=self.well_contact_active_width,
                                     height=self.well_contact_active_height)
                self.add_contact_center(self.body_contact.layer_stack, rotate=90,
                                        offset=vector(self.mid_x, y_offset),
                                        size=self.body_contact.dimensions)
            else:
                continue

            # cover with well
            if info["has_pwell"]:
                well_layer = PWELL if i == 0 else NWELL
                self.add_rect_center(well_layer, offset=vector(self.mid_x, y_offset),
                                     width=self.contact_nwell_width,
                                     height=self.contact_nwell_height)

    def add_output_pin(self):
        offset = vector(self.output_x, self.active_mid_y_nmos)
        self.add_layout_pin("Z", "metal1", offset=offset, height=self.active_mid_y_pmos - self.active_mid_y_nmos)

    def get_left_source_drain(self):
        """Return left-most source or drain"""
        return min(self.source_positions + self.drain_positions) - self.m1_width

    @staticmethod
    def equalize_nwell(module, top_left, top_right, bottom_left, bottom_right):
        input_list = [top_left, top_right, bottom_left, bottom_right]

        x_extension = lambda x: abs(0.5 * (x.mod.nwell_width - x.mod.width))
        x_extensions = list(map(x_extension, input_list))
        y_height = lambda x: x.mod.height - x.mod.mid_y
        y_heights = list(map(y_height, input_list))

        nwell_left = min(top_left.lx() - x_extensions[0], bottom_left.lx() - x_extensions[2])
        nwell_right = max(top_right.rx() + x_extensions[1], bottom_right.rx() + x_extensions[3])

        if "X" not in top_left.mirror and top_left == bottom_left:
            nwell_bottom = min(top_left.uy() - y_heights[0], top_right.uy() - y_heights[1])
            nwell_top = nwell_bottom + max(top_left.mod.nwell_height, top_right.mod.nwell_height)
        elif "X" in top_left.mirror and top_left == bottom_left:
            nwell_top = top_left.by() + max(y_heights)
            nwell_bottom = top_left.by()
        else:
            nwell_bottom = min(bottom_left.uy() - y_heights[2], bottom_right.uy() - y_heights[3])
            nwell_top = max(top_left.by() + y_heights[0], top_right.by() + y_heights[1])

        module.add_rect("nwell", offset=vector(nwell_left, nwell_bottom),
                        width=nwell_right - nwell_left,
                        height=nwell_top - nwell_bottom)

    def get_ptx_connections(self):
        raise NotImplementedError

    def add_ptx_inst(self):
        offset = vector(0, 0)
        self.pmos = ptx_spice(self.pmos_width, mults=self.tx_mults / self.num_tracks,
                              tx_type="pmos")
        self.add_mod(self.pmos)
        self.nmos = ptx_spice(self.nmos_width, mults=self.tx_mults / self.num_tracks,
                              tx_type="nmos")
        for index, conn_def in enumerate(self.get_ptx_connections()):
            mos, conn = conn_def
            name = "{}{}".format(mos.tx_type, index + 1)
            self.add_inst(name=name, mod=mos, offset=offset)
            self.connect_inst(conn)

    def create_pgate_tap(self):
        from pgates.pgate_tap import pgate_tap
        return pgate_tap(self)
