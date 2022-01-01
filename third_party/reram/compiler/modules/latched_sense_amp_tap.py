from base import design
from base.library_import import library_import


@library_import
class latched_sense_amp_tap(design.design):
    """
    Contains two bitline logic cells stacked vertically
    """
    pin_names = []
    lib_name = "latched_sense_amp_tap"
