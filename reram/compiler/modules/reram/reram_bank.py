import debug
from base import utils
from base.contact import m1m2, cross_m2m3, cross_m3m4, m2m3, m3m4
from base.design import METAL3, METAL4, METAL2, design
from base.layout_clearances import find_clearances, VERTICAL, HORIZONTAL
from base.vector import vector
from base.well_active_contacts import calculate_num_contacts
from modules.baseline_bank import BaselineBank, EXACT


class ReRamBank(BaselineBank):

    def add_pins(self):
        super().add_pins()
        self.add_pin_list(["vref", "vclamp", "vclampp"])
        for i in range(self.word_size):
            self.add_pin("DATA_OUT[{0}]".format(i))
        self.add_pin_list(["vdd_write", "vdd_wordline"])

    def get_mid_gnd_offset(self):
        """x offset for middle gnd rail"""
        return - self.wide_power_space - self.vdd_rail_width - self.bus_pitch

    def route_all_instance_power(self, inst, via_rotate=90):
        if inst == self.write_driver_array_inst:
            self.route_write_driver_power()
            return
        super().route_all_instance_power(inst, via_rotate)

    def get_precharge_y(self):
        # TODO: sky_tapeout: nwell to pwell min distance
        return super().get_precharge_y() + 0.05

    def get_bitcell_array_y_offset(self):
        # TODO: sky_tapeout: remove hard code
        y_offset = super().get_bitcell_array_y_offset() + 0.15
        return y_offset

    def get_wordline_offset(self):
        # leave space for wordline vdd
        offset = super().get_wordline_offset()
        offset.x -= (self.wide_power_space + self.vdd_rail_width)
        return offset

    def get_wordline_driver_connections(self):
        connections = super().get_wordline_driver_connections()
        return self.connections_from_mod(connections, [("vdd", "vdd_wordline")])

    def get_tri_state_connection_replacements(self):
        return [("out[", "DATA_OUT["),
                ("in_bar[", "sense_out_bar["), ("in[", "sense_out["),
                ("en", "tri_en", EXACT), ("en_bar", "tri_en_bar", EXACT)]

    def get_custom_net_destination(self, net):
        if net == "wordline_en":
            return self.precharge_array_inst.get_pins("br_reset")
        return super().get_custom_net_destination(net)

    def route_control_buffers(self):
        super().route_control_buffers()
        dest_pins = [(self.mid_vdd, self.right_vdd), (self.mid_gnd, self.right_gnd)]
        for i, pin_name in enumerate(["vdd", "gnd"]):
            left, right = dest_pins[i]
            for pin in self.control_buffers_inst.get_pins(pin_name):
                self.add_rect(METAL3, vector(left.lx(), pin.by()),
                              width=right.rx() - left.lx(), height=pin.height())
                for power_pin in dest_pins[i]:
                    self.add_contact_center(m2m3.layer_stack,
                                            offset=vector(power_pin.cx(), pin.cy()),
                                            size=[1, 2], rotate=90)
                    self.add_contact_center(m1m2.layer_stack,
                                            offset=vector(power_pin.cx(), pin.cy()),
                                            size=[1, 2], rotate=90)
                open_spaces = find_clearances(self, METAL2, direction=HORIZONTAL,
                                              region=(pin.by(), pin.uy()),
                                              existing=[(pin.lx(), pin.rx())])
                for open_space in open_spaces:
                    available_space = open_space[1] - open_space[0] - 2 * self.m2_space
                    if available_space <= 0:
                        continue
                    mid_via_x = 0.5 * (open_space[0] + open_space[1])
                    for via in [m1m2, m2m3]:
                        sample_contact = calculate_num_contacts(self, available_space,
                                                                layer_stack=via.layer_stack,
                                                                return_sample=True)
                        if available_space > sample_contact.h_1:
                            self.add_contact_center(via.layer_stack,
                                                    vector(mid_via_x, pin.cy()),
                                                    sample_contact.dimensions,
                                                    rotate=90)

    def route_sense_amp(self):
        """Routes sense amp power and connects write driver bitlines to sense amp bitlines"""
        debug.info(1, "Route sense amp")
        self.route_all_instance_power(self.sense_amp_array_inst)
        # write driver to sense amp

        sense_mod = self.sense_amp_array.child_mod
        clearances = find_clearances(sense_mod, METAL3, direction=VERTICAL)
        lowest = min(clearances, key=lambda x: x[0])

        sample_pin = sense_mod.get_pin("bl")
        y_shift = lowest[0] - sample_pin.by() + self.m3_space

        self.join_bitlines(top_instance=self.sense_amp_array_inst, top_suffix="",
                           bottom_instance=self.write_driver_array_inst,
                           bottom_suffix="", y_shift=y_shift)

        self.right_edge = self.right_gnd.rx() + self.m3_space

        for pin_name in ["vclamp", "vclampp", "vref"]:
            sense_pin = self.sense_amp_array_inst.get_pin(pin_name)
            self.add_layout_pin(pin_name, sense_pin.layer, sense_pin.lr(),
                                width=self.right_edge - sense_pin.rx())

    def route_bitcell(self):
        """wordline driver wordline to bitcell array wordlines"""
        for pin in self.bitcell_array_inst.get_pins("gnd"):
            x_offset = self.mid_gnd.lx()
            self.add_rect(pin.layer, vector(x_offset, pin.by()), height=pin.height(),
                          width=self.right_gnd.rx() - x_offset)
            self.add_power_via(pin, self.right_gnd)

    def get_write_driver_array_connection_replacements(self):
        replacements = super().get_write_driver_array_connection_replacements()
        replacements.append(("vdd", "vdd_write"))
        return replacements

    def route_write_driver_power(self):
        for pin in self.write_driver_array_inst.get_pins("gnd"):
            self.route_gnd_pin(pin)

        # TODO: sky_tapeout: handle separate vdd
        for pin in self.write_driver_array_inst.get_pins("vdd"):
            if pin.layer == METAL3:
                self.add_layout_pin("vdd_write", pin.layer, pin.ll(),
                                    height=pin.height(),
                                    width=self.right_edge - pin.lx())

    def connect_tri_output_to_data(self, word, fill_width, fill_height):
        tri_out_pin = self.tri_gate_array_inst.get_pin("out[{}]".format(word))
        x_offset = tri_out_pin.cx() - 0.5 * self.m4_width

        y_offset = tri_out_pin.uy() - 0.5 * m1m2.h_2
        self.add_layout_pin(f"DATA_OUT[{word}]", METAL4, vector(x_offset, self.min_point),
                            height=y_offset - self.min_point)
        via_offset = vector(tri_out_pin.cx(), y_offset)
        self.add_cross_contact_center(cross_m2m3, via_offset)
        self.add_cross_contact_center(cross_m3m4, via_offset, rotate=True, fill=False)

    def route_wordline_driver(self):
        self.route_wordline_in()
        self.route_wordline_enable()
        self.route_wl_to_bitcell()
        self.route_wordline_power()

    def route_wordline_enable(self):
        """route enable signal"""
        # TODO: sky_tapeout: merge with baseline
        en_pin = self.wordline_driver_inst.get_pin("en")
        en_rail = self.wordline_en_rail
        y_offset = en_pin.by() - self.m3_width - m2m3.w_2

        self.add_rect(METAL2, offset=en_rail.ul(), height=y_offset - en_rail.uy(),
                      width=self.bus_width)
        via_x = en_rail.rx() - 0.5 * m2m3.h_2
        self.add_cross_contact_center(cross_m2m3, offset=vector(via_x, y_offset),
                                      fill=False)
        self.add_rect(METAL3, offset=vector(en_pin.lx(), y_offset - 0.5 * self.bus_width),
                      width=en_rail.rx() - en_pin.lx(), height=self.bus_width)

    def route_wl_to_bitcell(self):
        for row in range(self.num_rows):
            pin_name = f"wl[{row}]"
            bitcell_pin = self.bitcell_array_inst.get_pin(pin_name)
            wl_pin = self.wordline_driver_inst.get_pin(pin_name)
            closest_y = min([wl_pin.uy(), wl_pin.by()],
                            key=lambda x: abs(bitcell_pin.cy() - x))

            via_x = wl_pin.lx() + 0.5 * m2m3.w_1
            design.add_cross_contact_center(self, cross_m2m3, vector(via_x, closest_y))
            path_x = bitcell_pin.lx() - self.m3_space - 0.5 * self.m3_width
            self.add_path(METAL3, [vector(via_x, closest_y),
                                   vector(path_x, closest_y),
                                   vector(path_x, bitcell_pin.cy()),
                                   vector(bitcell_pin.lx(), bitcell_pin.cy())])

    def route_wordline_power(self):
        # gnd
        for pin in self.wordline_driver_inst.get_pins("gnd"):
            self.add_rect(pin.layer, pin.lr(), height=pin.height(),
                          width=self.mid_gnd.rx() - pin.rx())
            self.add_power_via(pin, self.mid_gnd, 90)

        # vdd
        vdd_pins = list(sorted(self.wordline_driver_inst.get_pins("vdd"),
                               key=lambda x: x.by()))
        x_offset = self.mid_vdd.lx() - self.wide_power_space - self.vdd_rail_width
        y_offset = vdd_pins[0].by()
        self.vdd_wordline = self.add_layout_pin("vdd_wordline", METAL2,
                                                vector(x_offset, y_offset),
                                                width=self.vdd_rail_width,
                                                height=vdd_pins[-1].uy() - y_offset)

        for pin in vdd_pins:
            self.add_rect(pin.layer, pin.lr(), height=pin.height(),
                          width=self.vdd_wordline.rx() - pin.rx())
            self.add_power_via(pin, self.vdd_wordline, 90)

    def add_m2m4_power_rails_vias(self):
        power_pins = [self.mid_vdd, self.right_vdd, self.mid_gnd, self.right_gnd,
                      self.vdd_wordline]

        for pin in power_pins:
            self.add_layout_pin(pin.name, METAL4, offset=pin.ll(),
                                width=pin.width(),
                                height=pin.height())

            open_spaces = find_clearances(self, METAL3, direction=VERTICAL,
                                          region=(pin.lx(), pin.rx()),
                                          existing=[(pin.by(), pin.uy())])
            # TODO: sky_tapeout: remove duplicated logic
            for open_space in open_spaces:
                available_space = open_space[1] - open_space[0] - 2 * self.m3_space
                if available_space <= 0:
                    continue
                mid_via_y = 0.5 * (open_space[0] + open_space[1])
                for via in [m2m3, m3m4]:
                    sample_contact = calculate_num_contacts(self, available_space,
                                                            layer_stack=via.layer_stack,
                                                            return_sample=True)
                    if available_space > sample_contact.h_1:
                        self.add_contact_center(via.layer_stack,
                                                vector(pin.cx(), mid_via_y),
                                                size=[2, sample_contact.dimensions[1]])

    def connect_control_buffers_power_to_grid(self, grid_pin):
        pass

    def connect_m4_grid_instance_power(self, instance_pin, power_rail):
        related_pin = self.m4_pin_map[self.hash_m4_pin(power_rail)]
        for rail in [power_rail, related_pin]:
            if rail.by() <= instance_pin.cy() <= rail.uy():
                super().connect_m4_grid_instance_power(instance_pin, rail)

    def get_intra_array_grid_y(self):
        # TODO: sky_tapeout: move data out down so gnd pin can be connected
        top_gnd = max(self.write_driver_array_inst.get_pins("gnd"), key=lambda x: x.uy())
        return top_gnd.by() - self.m4_width

    def get_intra_array_grid_top(self):
        return self.bitcell_array_inst.uy()

    @staticmethod
    def hash_m4_pin(pin):
        return f"{utils.round_to_grid(pin.lx()):.3g}"

    def add_related_m4_grid_pin(self, original_pin):
        if not hasattr(self, "m4_pin_map"):
            self.m4_pin_map = {}
        pin_bottom = self.tri_gate_array_inst.get_pins("vdd")[0].by() - self.m4_width

        bot_gnd = min(self.write_driver_array_inst.get_pins("gnd"), key=lambda x: x.uy())

        pin_top = bot_gnd.uy() + self.m4_width
        pin = self.add_layout_pin(original_pin.name, original_pin.layer,
                                  vector(original_pin.lx(), pin_bottom),
                                  width=original_pin.width(),
                                  height=pin_top - pin_bottom)
        self.m4_pin_map[self.hash_m4_pin(original_pin)] = pin
