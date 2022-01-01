from base import design
from base.library_import import library_import
from globals import OPTS


@library_import
class write_driver(design.design):
    """
    Tristate write driver to be active during write operations only.       
    This module implements the write driver cell used in the design. It
    is a hand-made cell, so the layout and netlist should be available in
    the technology library.
    """

    lib_name = OPTS.write_driver_mod
