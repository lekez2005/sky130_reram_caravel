import os

from base.design import design
from base.library_import import library_import
from base.vector import vector

wrapper_name = "user_analog_project_wrapper"


@library_import
class EmptyWrapper(design):
    lib_name = "user_analog_project_wrapper_empty"


@library_import
class Sram(design):
    lib_name = None


class ReRamWrapper(design):
    def __init__(self, gds_file):
        design.__init__(self, wrapper_name)
        self.gds_file = gds_file
        self.create_layout()

    def create_layout(self):
        self.load_empty_wrapper()
        self.load_sram()
        self.add_modules()

    def load_empty_wrapper(self):
        self.empty_wrapper = EmptyWrapper()

    def load_sram(self):
        sram_name = os.path.splitext(os.path.basename(self.gds_file))[0]
        Sram.lib_name = sram_name
        self.sram = Sram()

    def add_modules(self):
        self.width = self.empty_wrapper.width
        self.height = self.empty_wrapper.height
        self.add_boundary()

        self.wrapper_inst = self.add_inst(self.empty_wrapper.name, self.empty_wrapper,
                                          vector(0, 0))
        self.connect_inst([], check=False)

        x_offset = 0.5 * self.width - 0.5 * self.sram.width
        y_offset = 0.5 * self.height - 0.5 * self.sram.height

        module_name = os.path.splitext(os.path.basename(self.gds_file))[0]
        self.sram_inst = self.add_inst(module_name, self.sram,
                                       vector(x_offset, y_offset))
        self.connect_inst([], check=False)


def wrap_reram(gds_file):
    global gds_dir
    gds_dir = os.path.dirname(gds_file)

    wrapper = ReRamWrapper(gds_file)
    output_file = os.path.join(gds_dir, f"{wrapper_name}.gds")
    wrapper.gds_write(output_file)
    return wrapper
