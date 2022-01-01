from abc import ABC

import tech
from base import utils
from base.contact import well as well_contact, poly as poly_contact, cross_poly, m1m2, m2m3, cross_m1m2, cross_m2m3
from base.design import design, ACTIVE, METAL1, POLY, METAL3, METAL2, PWELL, NWELL
from base.layout_clearances import find_clearances, HORIZONTAL
from base.unique_meta import Unique
from base.utils import round_to_grid as round_
from base.vector import vector
from base.well_active_contacts import calculate_num_contacts
from globals import OPTS
from pgates.ptx import ptx
from pgates.ptx_spice import ptx_spice


class BitcellAlignedPgate(design, metaclass=Unique):
    mod_name = None

    def create_layout(self):
        raise NotImplemented

    @classmethod
    def get_name(cls, size, name=None):
        raise NotImplemented

    def __init__(self, size, name=None):
        name = name or self.get_name(size, name)
        self.size = size
        design.__init__(self, name)
        self.create_layout()

    @staticmethod
    def get_sorted_pins(tx_inst, pin_name):
        return list(sorted(tx_inst.get_pins(pin_name), key=lambda x: x.lx()))

    def create_ptx(self, size, is_pmos=False, **kwargs):
        width = size * tech.spice["minwidth_tx"]
        if is_pmos:
            width *= tech.parameter["beta"]
        return self.create_ptx_by_width(width, is_pmos, **kwargs)

    def create_ptx_by_width(self, width, is_pmos=False, **kwargs):
        if is_pmos:
            tx_type = "pmos"
        else:
            tx_type = "nmos"
        tx = ptx(width=width, tx_type=tx_type, **kwargs)
        self.add_mod(tx)
        return tx

    def create_ptx_spice(self, tx: ptx, mults=1, scale=1):
        tx_spice = ptx_spice(width=tx.tx_width * scale, mults=mults,
                             tx_type=tx.tx_type, tx_length=tx.tx_length)
        self.add_mod(tx_spice)
        return tx_spice

    def flatten_tx(self, *args):
        if not args:
            args = [x for x in self.insts if isinstance(x.mod, ptx) and
                    not isinstance(x.mod, ptx_spice)]
        for tx_inst in args:
            ptx.flatten_tx_inst(self, tx_inst)

    def create_modules(self):
        self.bitcell = self.create_mod_from_str(OPTS.bitcell)
        self.width = self.bitcell.width
        self.mid_x = utils.round_to_grid(0.5 * self.width)

    def calculate_bottom_space(self):
        well_contact_mid_y = 0.5 * self.rail_height
        well_contact_active_top = well_contact_mid_y + 0.5 * well_contact.first_layer_width
        return well_contact_active_top + self.get_space(ACTIVE)

    def add_mid_poly_via(self, nmos_poly, mid_y, min_via_x=None):
        horz_poly = poly_contact.first_layer_width > nmos_poly[0].width()
        x_offsets = []

        for i in [1, 2]:
            # add poly contact
            if horz_poly:
                x_offset = min_via_x or nmos_poly[i].cx()
            else:
                x_offset = nmos_poly[i].cx()
            x_offsets.append(x_offset)

            if horz_poly and i == 1:
                self.add_cross_contact_center(cross_poly, vector(x_offset, mid_y))
            elif not horz_poly:
                self.add_contact_center(poly_contact.layer_stack, vector(x_offset, mid_y))

        # horizontal join poly contact
        layer = POLY if horz_poly else METAL1
        height = (poly_contact.first_layer_height
                  if horz_poly else poly_contact.second_layer_height)
        self.add_rect(layer, vector(nmos_poly[1].cx(), mid_y - 0.5 * height),
                      height=height, width=nmos_poly[2].cx() - nmos_poly[1].cx())

        return x_offsets[0]

    def calculate_poly_via_offsets(self, tx_inst):
        poly_rects = self.get_sorted_pins(tx_inst, "G")
        left_via_x = poly_rects[0].rx() - 0.5 * poly_contact.w_1
        right_via_x = poly_rects[1].lx() + 0.5 * poly_contact.w_1
        return left_via_x, right_via_x

    def join_poly(self, nmos_inst, pmos_inst, indices=None, mid_y=None):
        all_nmos_poly = self.get_sorted_pins(nmos_inst, "G")
        all_pmos_poly = self.get_sorted_pins(pmos_inst, "G")
        if indices is None:
            num_poly = len(all_nmos_poly)
            indices = [(i, i) for i in range(num_poly)]

        for nmos_index, pmos_index in indices:
            nmos_poly = all_nmos_poly[nmos_index]
            pmos_poly = all_pmos_poly[pmos_index]
            bottom_poly, top_poly = sorted([nmos_poly, pmos_poly], key=lambda x: x.by())
            width = nmos_poly.width()
            if round_(bottom_poly.lx()) == round_(top_poly.lx()):
                self.add_rect(POLY, bottom_poly.ul(), width=width,
                              height=top_poly.by() - bottom_poly.uy())
            else:
                if mid_y is None:
                    mid_y = 0.5 * (bottom_poly.uy() + top_poly.by()) - 0.5 * width
                self.add_rect(POLY, bottom_poly.ul(), width=width,
                              height=mid_y + width - bottom_poly.uy())
                self.add_rect(POLY, vector(bottom_poly.lx(), mid_y), height=width,
                              width=top_poly.cx() - bottom_poly.lx())
                self.add_rect(POLY, vector(top_poly.lx(), mid_y), width=width,
                              height=top_poly.by() - mid_y)

    def extend_tx_well(self, tx_inst, well_type, pin, cont=None):
        if tech.info[f"has_{well_type}"]:
            if cont is not None:
                well_width = cont.mod.first_layer_height + 2 * self.well_enclose_active
                well_width = max(self.width, well_width)
            else:
                well_width = self.width

            ptx_rects = tx_inst.get_layer_shapes(well_type)
            ptx_rect = max(ptx_rects, key=lambda x: x.width * x.height)
            well_width = max(well_width, ptx_rect.width)

            x_offset = 0.5 * (self.width - well_width)

            if pin.cy() < tx_inst.cy():
                well_top = ptx_rect.uy()
                well_bottom = (pin.cy() - 0.5 * well_contact.first_layer_width -
                               self.well_enclose_active)
            else:
                well_top = (pin.cy() + 0.5 * well_contact.first_layer_width +
                            self.well_enclose_active)
                well_bottom = ptx_rect.by()
            self.add_rect(well_type, vector(x_offset, well_bottom), width=well_width,
                          height=well_top - well_bottom)

    def add_power_tap(self, y_offset, pin_name, tx_inst, add_m3=True):
        if pin_name == "gnd":
            well_type = PWELL
        else:
            well_type = NWELL
        implant_type = well_type[0]

        max_width = self.width - self.get_space(ACTIVE)
        num_contacts = calculate_num_contacts(self, max_width,
                                              layer_stack=well_contact.layer_stack,
                                              return_sample=False)
        pin_width = self.width
        if add_m3:
            pin_width += max(m1m2.first_layer_height, m2m3.second_layer_height)
        x_offset = 0.5 * (self.width - pin_width)
        pin = self.add_layout_pin(pin_name, METAL1, offset=vector(x_offset, y_offset),
                                  height=self.rail_height, width=pin_width)
        cont = self.add_contact_center(well_contact.layer_stack, pin.center(), rotate=90,
                                       size=[1, num_contacts],
                                       implant_type=implant_type,
                                       well_type=well_type)

        # add well
        self.extend_tx_well(tx_inst, well_type, pin, cont)

        if not add_m3:
            return pin, cont, well_type

        self.add_layout_pin(pin.name, METAL3, pin.ll(), width=pin.width(),
                            height=pin.height())
        open_spaces = find_clearances(self, layer=METAL2, direction=HORIZONTAL,
                                      region=(pin.by(), pin.uy()))

        min_space = (max(m1m2.second_layer_width, m2m3.first_layer_width) +
                     2 * self.get_parallel_space(METAL2))
        half_space = utils.round_to_grid(0.5 * min_space)

        for space in open_spaces:
            space = [utils.round_to_grid(x) for x in space]
            extent = utils.round_to_grid(space[1] - space[0])
            if space[0] == 0.0:
                mid_contact = 0
                if extent <= half_space:
                    continue
            elif space[1] == utils.round_to_grid(self.width):
                mid_contact = self.width
                if extent <= half_space:
                    continue
            else:
                if extent <= min_space:
                    continue
                mid_contact = utils.round_to_grid(0.5 * (space[0] + space[1]))
            offset = vector(mid_contact, pin.cy())
            self.add_cross_contact_center(cross_m1m2, offset, rotate=True)
            self.add_cross_contact_center(cross_m2m3, offset, rotate=False)

        return pin, cont, well_type

    def route_pin_to_power(self, pin_name, pin):
        power_pins = self.get_pins(pin_name)
        power_pin = min(power_pins, key=lambda x: abs(x.cy() - pin.cy()))
        self.add_rect(METAL1, vector(pin.lx(), pin.cy()), width=pin.width(),
                      height=power_pin.cy() - pin.cy())

    def route_tx_to_power(self, tx_inst, tx_pin_name="D"):
        pin_name = "vdd" if tx_inst.mod.tx_type.startswith("p") else "gnd"
        power_pins = self.get_pins(pin_name)
        power_pin = min(power_pins, key=lambda x: abs(x.cy() - tx_inst.cy()))
        for tx_pin in tx_inst.get_pins(tx_pin_name):
            # todo make configurable
            width = round_(1.5 * self.m1_width)
            if tx_pin.cy() >= power_pin.cy():
                y_offset = tx_pin.uy()
            else:
                y_offset = tx_pin.by()
            self.add_rect(METAL1, vector(tx_pin.cx() - 0.5 * width, y_offset),
                          width=width, height=power_pin.cy() - y_offset)

    @staticmethod
    def calculate_active_to_poly_cont_mid(tx_type):
        """Distance from edge of active to middle of poly contact"""
        active_to_poly_contact = tech.drc.get(f"poly_contact_to_{tx_type[0]}_active",
                                              tech.drc["poly_contact_to_active"])
        return active_to_poly_contact + 0.5 * poly_contact.contact_width
