import tech
from base.contact import well as well_contact, m1m2, m2m3
from base.design import METAL2, NWELL, PWELL, METAL3
from base.layout_clearances import find_clearances, HORIZONTAL
from base.vector import vector
from base.well_active_contacts import calculate_num_contacts
from modules.stacked_hierarchical_decoder import stacked_hierarchical_decoder


class reram_row_decoder(stacked_hierarchical_decoder):
    def create_layout(self):
        super().create_layout()
        tech.add_tech_layers(self)

    def copy_power_pin(self, pin):
        if pin.uy() <= self.row_decoder_min_y:
            x_offset = 0
        else:
            x_offset = self.power_rail_x
        right_x = self.inv_inst[1].lx()
        self.add_layout_pin(pin.name, pin.layer, offset=vector(x_offset, pin.by()),
                            width=right_x - x_offset, height=pin.height())

    def add_body_contacts(self):
        pass

    def route_vdd_gnd(self):
        super().route_vdd_gnd()

        nwell_height = max(well_contact.first_layer_width + 2 * self.well_enclose_active,
                           self.well_width)
        self.contact_nwell_height = nwell_height

        fill_height = m1m2.w_2
        _, min_fill_width = self.calculate_min_area_fill(fill_height, layer=METAL2)

        def calculate_sample_vias(sample_pin):
            open_spaces = find_clearances(self, layer=METAL2, direction=HORIZONTAL,
                                          region=(sample_pin.by(), sample_pin.uy()),
                                          existing=[(sample_pin.lx(), sample_pin.rx())])
            vias = []
            for open_space in open_spaces:
                open_space = [open_space[0], min(open_space[1], sample_pin.rx())]
                available_space = open_space[1] - open_space[0] - 2 * self.m2_space
                if available_space <= 0:
                    continue
                mid_via_x = 0.5 * (open_space[0] + open_space[1])
                for via in [m1m2, m2m3]:
                    sample_contact = calculate_num_contacts(self, available_space,
                                                            layer_stack=via.layer_stack,
                                                            return_sample=True)
                    if available_space > sample_contact.h_2 > min_fill_width:
                        vias.append((mid_via_x, sample_contact))
            return vias

        for pin_name in ["vdd", "gnd"]:
            layout_pins = list(sorted(self.get_pins(pin_name), key=lambda x: x.by()))
            row_decoder_pins = [x for x in layout_pins if x.uy() > self.predecoder_height]

            row_decoder_vias = calculate_sample_vias(row_decoder_pins[-1])

            well_contact_x = 0.5 * (self.nand_inst[0].cx() + self.nand_inst[1].cx())
            left_inst, right_inst = self.nand_inst[:2]

            nwell_width = right_inst.lx() - left_inst.rx() + 2 * self.well_width
            sample_well = calculate_num_contacts(self, right_inst.lx() - left_inst.rx(),
                                                 layer_stack=well_contact.layer_stack,
                                                 return_sample=True)
            well_type = PWELL if pin_name == "gnd" else NWELL

            for pin in layout_pins:
                if pin_name == "vdd" and pin in row_decoder_pins:
                    self.add_rect_center(NWELL, offset=(well_contact_x, pin.cy()),
                                         width=nwell_width, height=nwell_height)
                if pin in row_decoder_pins:
                    offset = vector(well_contact_x, pin.cy())
                    self.add_contact_center(well_contact.layer_stack, offset, rotate=90,
                                            size=sample_well.dimensions,
                                            implant_type=well_type[0],
                                            well_type=well_type)
                    vias = row_decoder_vias
                else:
                    vias = calculate_sample_vias(pin)

                self.add_layout_pin(pin_name, METAL3, pin.ll(), width=pin.width(),
                                    height=pin.height())
                for mid_x, sample_via in vias:
                    self.add_contact_center(sample_via.layer_stack, vector(mid_x, pin.cy()),
                                            size=sample_via.dimensions, rotate=90)
