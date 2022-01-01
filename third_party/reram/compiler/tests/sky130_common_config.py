
setup_time = 0.1  # in nanoseconds
tech_name = "sky130"
process_corners = ["TT"]
supply_voltages = [1.8]
temperatures = [25]

logic_buffers_height = 3.9

control_buffers_num_rows = 1

# technology
analytical_delay = False
spice_name = "spectre"
tran_options = " errpreset=moderate "

# characterization parameters
default_char_period = 4e-9
enhance_pgate_pins = True


def configure_char_timing(options, class_name):
    if class_name == "FO4DelayCharacterizer":
        return 800e-12
    return default_char_period
