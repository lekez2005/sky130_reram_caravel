import debug
from globals import OPTS
from modules.tri_gate_array import tri_gate_array


class TriStatePgateArray(tri_gate_array):
    def create_child_mod(self):
        buffer_size = OPTS.tri_state_buffer_size
        self.child_mod = self.create_mod_from_str(self.mod_name, size=buffer_size)
        debug.info(1, "Using module {} for {}".format(self.child_mod.name,
                                                      self.name))
        self.height = self.child_mod.height

    def add_layout_pins(self):
        super().add_layout_pins()
        for pin_name in ["en", "en_bar"]:
            pin = self.child_mod.get_pin(pin_name)
            self.add_layout_pin(pin_name, pin.layer, pin.ll(), height=pin.height(),
                                width=self.width - pin.lx())

    def get_horizontal_pins(self):
        for pin_name in ["vdd", "gnd", "en", "en_bar"]:
            for pin in self.child_mod.get_pins(pin_name):
                yield pin
