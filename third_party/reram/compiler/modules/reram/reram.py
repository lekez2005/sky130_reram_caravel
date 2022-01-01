import debug
import tech
from base import utils
from base.contact import m1m2, m2m3, cross_m2m3, cross_m3m4, m3m4
from base.design import METAL1, METAL2, METAL3, METAL4, METAL5
from base.layout_clearances import find_clearances, VERTICAL
from base.vector import vector
from base.well_active_contacts import calculate_num_contacts
from modules.baseline_sram import BaselineSram


class ReRam(BaselineSram):

    def add_row_decoder(self):
        # TODO: sky_tapeout: calculate x offset
        if self.num_rows == 16:
            x_offset = -12
        else:
            x_offset = -18
        self.row_decoder_inst = self.add_inst(name="row_decoder", mod=self.row_decoder,
                                              offset=vector(x_offset, self.row_decoder_y))

        self.connect_inst(self.get_row_decoder_connections())

    def join_decoder_wells(self):
        pass

    def fill_decoder_wordline_space(self):
        pass

    def add_pins(self):
        """ Adding pins for Bank module"""
        for inst in [self.bank_inst, self.row_decoder_inst]:
            conn_index = self.insts.index(inst)
            inst_conns = self.conns[conn_index]
            for net in inst_conns:
                count = 0
                for i, conns in enumerate(self.conns):
                    if net in conns:
                        count += 1
                pin_index = inst_conns.index(net)
                if count == 1:
                    debug.info(2, "Add inst %s layout pin %s as %s", inst.name,
                               inst.mod.pins[pin_index], net)
                    self.add_pin(net)
        self.add_pin_list(["clk", "vdd", "gnd"])

    def route_decoder_power(self):
        rails = [self.mid_vdd, self.mid_gnd]
        pin_names = ["vdd", "gnd"]
        for rail, pin_name in zip(rails, pin_names):
            for pin in self.row_decoder_inst.get_pins(pin_name):
                self.add_rect(pin.layer, vector(rail.lx(), pin.by()), height=pin.height(),
                              width=pin.lx() - rail.lx())
                via = m1m2 if pin.layer == METAL1 else m2m3
                self.add_contact_center(via.layer_stack, vector(rail.cx(), pin.cy()),
                                        size=[1, 2], rotate=90)

        self.add_m2_m4_power()
        self.route_write_driver_power()
        self.route_wordline_power()

    def route_decoder_outputs(self):
        for row in range(self.num_rows):
            bank_pin = self.bank_inst.get_pin(f"dec_out[{row}]")
            decoder_pin = self.row_decoder_inst.get_pin(f"decode[{row}]")
            closest_y = min([decoder_pin.uy(), decoder_pin.by()],
                            key=lambda x: abs(bank_pin.cy() - x))

            y_offset = bank_pin.cy()
            x_offset = decoder_pin.cx() - 0.5 * self.m2_width
            self.add_rect(METAL2, vector(x_offset, closest_y), height=y_offset - closest_y)
            self.add_cross_contact_center(cross_m2m3, vector(decoder_pin.cx(), y_offset),
                                          fill=False)
            self.add_rect(METAL3, vector(x_offset, y_offset - 0.5 * self.m3_width),
                          width=bank_pin.lx() - x_offset)

    def add_address_pins(self):

        current_y_base = self.bank_inst.uy()
        pin_count = 0
        pin_space = 0.5 * self.rail_height + self.m3_space + self.bus_pitch
        clk_pin = max(self.row_decoder_inst.get_pins("clk"), key=lambda x: x.rx())
        x_offset = clk_pin.lx() - self.m4_space - self.m4_width

        pitch = self.m4_space + m3m4.h_2

        for i in range(self.row_addr_size):
            pin_name = "A[{}]".format(i).lower()
            existing_pin = self.row_decoder_inst.get_pin(pin_name.lower())
            bottom_y = utils.round_to_grid(existing_pin.by())
            if not bottom_y == current_y_base:
                current_y_base = bottom_y
                pin_count = 0

            y_offset = bottom_y - pin_space - pin_count * self.bus_pitch

            mid_x = x_offset + 0.5 * self.m4_width
            via_y = y_offset + 0.5 * self.bus_width

            self.add_rect(METAL2, vector(existing_pin.lx(), y_offset),
                          width=existing_pin.width(), height=existing_pin.by() - y_offset)

            self.add_cross_contact_center(m2m3, vector(existing_pin.cx(), via_y), fill=False)

            self.add_rect(METAL3, vector(x_offset, via_y - 0.5 * self.bus_width),
                          height=self.bus_width, width=existing_pin.cx() - x_offset)

            self.add_cross_contact_center(m3m4, vector(mid_x, via_y),
                                          fill=False, rotate=True)
            self.add_layout_pin(f"ADDR[{i}]", METAL4, vector(x_offset, self.min_point),
                                height=via_y - self.min_point)

            x_offset -= pitch
            pin_count += 1

    def copy_layout_pins(self):
        self.add_address_pins()
        exceptions = ["vdd", "gnd", "vdd_wordline", "vdd_write"]
        for pin in self.pins:
            if pin.lower() in self.pin_map or pin in exceptions:
                continue

            for inst in [self.bank_inst, self.row_decoder_inst]:
                conn_index = self.insts.index(inst)
                inst_conns = self.conns[conn_index]
                if pin in inst_conns:
                    pin_index = inst_conns.index(pin)
                    debug.info(1, "Copy inst %s layout pin %s to %s", inst.name,
                               inst.mod.pins[pin_index], pin)
                    self.copy_layout_pin(inst, inst.mod.pins[pin_index], pin)
                    break
        tech.add_tech_layers(self)

    def route_write_driver_power(self):
        self.power_grid_width = getattr(tech, "power_grid_width", self.m4_width)

        power_grid_y_space = getattr(tech, "power_grid_y_space",
                                     self.get_wide_space("metal6"))

        power_grid_pitch = self.power_grid_width + power_grid_y_space

        pin = self.bank_inst.get_pin("vdd_write")
        y_offset = pin.cy() - 0.5 * self.power_grid_width
        self.add_layout_pin("vdd_write", METAL5, vector(pin.lx(), y_offset),
                            width=pin.width(), height=self.power_grid_width)
        space = 0.5 * power_grid_pitch
        self.power_grid_y_forbidden = [(pin.cy() - space - self.power_grid_width,
                                        pin.cy() + space)]

    def route_wordline_power(self):
        pin = self.bank_inst.get_pin("vdd_wordline")
        x_offset = pin.cx() - 0.5 * self.power_grid_width
        self.add_layout_pin("vdd_wordline", "metal6", vector(x_offset, pin.by()),
                            width=self.power_grid_width, height=pin.height())

        power_grid_x_space = getattr(tech, "power_grid_x_space",
                                     self.get_wide_space(METAL5))
        power_grid_pitch = self.power_grid_width + power_grid_x_space

        space = 0.5 * power_grid_pitch + self.power_grid_width
        # TODO: sky_tapeout: remove duplicated logic
        self.power_grid_x_forbidden = [(pin.cx() - space - 15,
                                        pin.cx() + space)]

    def get_power_grid_forbidden_regions(self):
        return self.power_grid_x_forbidden, self.power_grid_y_forbidden

    def add_m2_m4_power(self):
        self.m4_gnd_rects = []
        self.m4_vdd_rects = []
        self.m4_power_pins = m4_power_pins = (self.bank_inst.get_pins("vdd") +
                                              self.bank_inst.get_pins("gnd"))
        rails = [self.mid_vdd, self.mid_gnd]
        pin_names = ["vdd", "gnd"]
        for pin_name, rail in zip(pin_names, rails):
            m4_pin = self.add_layout_pin(pin_name, METAL4, rail.ll(), width=rail.width,
                                         height=rail.height)
            m4_power_pins.append(m4_pin)
            open_spaces = find_clearances(self, METAL3, direction=VERTICAL,
                                          region=(m4_pin.lx(), m4_pin.rx()),
                                          existing=[(m4_pin.by(), m4_pin.uy())])
            # TODO: sky_tapeout: remove duplicated logic
            min_space = 1
            for open_space in open_spaces:
                available_space = open_space[1] - open_space[0] - min_space
                if available_space <= 0:
                    continue
                mid_via_y = 0.5 * (open_space[0] + open_space[1])
                for via in [m2m3, m3m4]:
                    sample_contact = calculate_num_contacts(self, available_space,
                                                            layer_stack=via.layer_stack,
                                                            return_sample=True)
                    if available_space > sample_contact.h_1:
                        self.add_contact_center(via.layer_stack,
                                                vector(rail.cx(), mid_via_y),
                                                size=[2, sample_contact.dimensions[1]])

    def route_power_grid(self):
        super().route_power_grid()

        write_pin = self.get_pin("vdd_write")

        m4m5 = ("metal4", "via4", "metal5")
        m5m6 = ("metal5", "via5", "metal6")

        fill_width = m3m4.w_2
        _, fill_height = self.calculate_min_area_fill(fill_width, layer=METAL4)

        right_vdd = max(self.bank_inst.get_pins("vdd"), key=lambda x: x.rx())

        vdd_rects = self.m4_vdd_rects
        for rect in vdd_rects:
            if (write_pin.lx() <= rect.cx() <= write_pin.rx() and
                    rect.cx() <= right_vdd.lx()):
                offset = vector(rect.cx(), write_pin.cy())
                self.add_cross_contact_center(cross_m3m4, offset, fill=False,
                                              rotate=True)
                self.add_contact_center(m4m5, offset)
                self.add_rect_center(METAL4, offset, width=fill_width, height=fill_height)

        wordline_pin = self.get_pin("vdd_wordline")

        open_spaces = find_clearances(self, METAL5, direction=VERTICAL,
                                      region=(wordline_pin.lx(), wordline_pin.rx()),
                                      existing=[(wordline_pin.by(), wordline_pin.uy())])

        # adjust tech.power_grid_y_space until there is space for wordline via
        # TODO: sky_tapeout: remove duplicated logic
        for open_space in open_spaces:
            mid_via_y = 0.5 * (open_space[0] + open_space[1])
            offset = vector(wordline_pin.cx(), mid_via_y)
            self.add_contact_center(m4m5, offset)
            self.add_contact_center(m5m6, offset)
