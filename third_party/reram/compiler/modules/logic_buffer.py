import debug
from base import contact
from base import design
from base.unique_meta import Unique
from base.vector import vector
from modules.buffer_stage import BufferStage
from pgates.pinv import pinv
from pgates.pnand2 import pnand2
from pgates.pnand3 import pnand3
from pgates.pnor2 import pnor2
from pgates.pnor3 import pnor3


class LogicBuffer(design.design, metaclass=Unique):
    """
    Buffers the in pin
    The last output is labeled out and the penultimate output is labelled out_bar regardless of number of buffer stages
    """
    logic_mod = logic_inst = buffer_mod = buffer_inst = None

    PNAND_3 = "pnand3"

    def __init__(self, buffer_stages, logic="pnand2", height=None, route_inputs=True, route_outputs=True, contact_pwell=True,
                 contact_nwell=True, align_bitcell=False):
        if buffer_stages is None or len(buffer_stages) < 1:
            debug.error("There should be at least one buffer stage", 1)

        self.buffer_stages = buffer_stages
        self.inverting_output = len(buffer_stages) % 2 == 1
        self.contact_pwell = contact_pwell
        self.contact_nwell = contact_nwell
        self.route_inputs = route_inputs
        self.route_outputs = route_outputs
        self.align_bitcell = align_bitcell
        self.logic = logic

        design.design.__init__(self, self.name)
        debug.info(2, "Create logic buffers with stages: [{}] ".format(",".join(map(str, buffer_stages))))

        self.height = height

        self.create_layout()
        self.DRC_LVS()

    @classmethod
    def get_name(cls, buffer_stages, logic="pnand2", height=None, route_inputs=True, route_outputs=True, contact_pwell=True,
                 contact_nwell=True, align_bitcell=False):
        name = "logic_buffer_{}_{}".format(logic, "_".join(['{:.3g}'.format(x) for x in buffer_stages]))
        if not route_inputs:
            name += "_no_in"
        if not route_outputs:
            name += "_no_out"
        if not contact_nwell:
            name += "_no_nwell_cont"
        if not contact_pwell:
            name += "_no_pwell_cont"
        if not height == pinv.bitcell.height:
            name += "_h_{:.2g}".format(height).replace('.', '_')
        if align_bitcell:
            name += "_align"
        return name.replace(".", "_")

    def add_pins(self):
        self.add_pin_list(self.logic_mod.pins[:-3])
        self.add_pin("out_inv")
        self.add_pin("out")
        self.add_pin("vdd")
        self.add_pin("gnd")

    def create_layout(self):
        self.create_modules()
        self.add_pins()
        self.add_modules()

        self.route_input_pins()
        self.route_out_pins()
        self.route_power_pins()

        self.width = self.buffer_inst.rx()
        self.height = self.buffer_inst.height

    def create_modules(self):
        if self.logic == "pnand2":
            logic_class = pnand2
        elif self.logic == "pnor2":
            logic_class = pnor2
        elif self.logic == self.PNAND_3:
            logic_class = pnand3
        elif self.logic == "pnor3":
            logic_class = pnor3
        else:
            raise Exception("Invalid logic selected")
        self.logic_mod = logic_class(size=1, height=self.height, contact_nwell=self.contact_nwell,
                                     same_line_inputs=self.align_bitcell,
                                     contact_pwell=self.contact_pwell,
                                     align_bitcell=self.align_bitcell)

        self.add_mod(self.logic_mod)
        self.create_buffer_mod()

    def create_buffer_mod(self):
        self.buffer_mod = BufferStage(self.buffer_stages, height=self.height,
                                      route_outputs=self.route_outputs,
                                      contact_pwell=self.contact_pwell,
                                      contact_nwell=self.contact_nwell,
                                      align_bitcell=self.align_bitcell)
        self.add_mod(self.buffer_mod)

    def add_modules(self):
        self.logic_inst = self.add_inst("logic", mod=self.logic_mod, offset=vector(0, 0))
        connections = [x for x in self.logic_mod.pins]
        if len(self.buffer_stages) == 1:
            intermediate_net = "out_inv"
            buffer_conns = ["out_inv", "out", "out_inv"]
        else:
            intermediate_net = "logic_out"
            buffer_conns = [intermediate_net, "out", "out_inv"]
        connections[-3] = intermediate_net
        self.connect_inst(connections)

        self.buffer_inst = self.add_inst("buffer", mod=self.buffer_mod,
                                         offset=self.logic_inst.lr())

        self.connect_inst(buffer_conns + ["vdd", "gnd"])

    def route_input_pins(self):
        # connect input pins
        if self.route_inputs:
            pins = sorted([self.logic_inst.get_pin("A"), self.logic_inst.get_pin("B")], key=lambda x: x.cy(),
                          reverse=True)
            rail_x = 0.5 * self.m2_width
            for i in range(len(pins)):
                pin = pins[i]
                self.add_rect("metal2", offset=vector(rail_x, pin.cy() - 0.5 * self.m2_width), width=pin.cx() - rail_x)
                if i == 1 and self.logic == "pnor2":
                    self.add_contact(contact.m1m2.layer_stack, offset=pin.lr(), rotate=90)
                elif i == 1:
                    self.add_contact(layers=contact.contact.m1m2_layers, offset=pin.lr(), rotate=90)
                else:
                    y_offset = pin.uy() - contact.m1m2.second_layer_height
                    self.add_contact(layers=contact.contact.m1m2_layers, offset=vector(pin.lx(), y_offset))
                self.add_layout_pin(pin.name, "metal2", offset=vector(rail_x, 0), height=pin.cy())
                rail_x += self.parallel_line_space + self.m2_width
        else:
            for pin_name in self.logic_mod.pins[:-3]:
                self.copy_layout_pin(self.logic_inst, pin_name)

        # logic output to buffer input
        logic_out = self.logic_inst.get_pin("Z")
        buffer_in = self.buffer_inst.get_pin("in")
        self.join_logic_out_to_buffer_in(logic_out, buffer_in)

    def join_logic_out_to_buffer_in(self, logic_out, buffer_in):
        self.add_rect("metal1", offset=vector(logic_out.cx(), buffer_in.cy() - 0.5*self.m1_width),
                      width=buffer_in.lx() - logic_out.cx())

    def route_out_pins(self):
        if len(self.buffer_stages) == 1:
            self.copy_layout_pin(self.logic_inst, "Z", "out_inv")
        else:
            self.copy_layout_pin(self.buffer_inst, "out", "out_inv")
        self.copy_layout_pin(self.buffer_inst, "out_inv", "out")

    def route_power_pins(self):
        pin_names = ["vdd", "gnd"]
        pins = [self.buffer_inst.get_pin("vdd"), self.buffer_inst.get_pin("gnd")]
        for i in range(2):
            pin = pins[i]
            self.add_layout_pin(pin_names[i], pin.layer, offset=vector(0, pin.by()),
                                height=pin.height(), width=pin.rx())
