from config_baseline import *
from sky130_common_config import *
from config_reram_base import *
# from tests.reram.config_reram_base import *

bitcell_tx_size = 7  # bitcell access device size in um
bitcell_tx_mults = 4  # number of access device fingers
bitcell_width = 2.5  # bitcell width in um

symmetric_bitcell = False
mirror_bitcell_y_axis = True
use_x_body_taps = False
use_y_body_taps = True

bitcell_array = "reram_bitcell_array.ReRamBitcellArray"

wordline_driver = "reram_wordline_driver_array"
decoder = "reram_row_decoder.reram_row_decoder"

precharge = "bitline_discharge.BitlineDischarge"
precharge_size = 6

ms_flop = "ms_flop_clk_buf.MsFlopClkBuf"
ms_flop_horz_pitch = "ms_flop_horz_pitch.MsFlopHorzPitch"
predecoder_flop = "ms_flop_horz_pitch.MsFlopHorzPitch"
control_flop = "ms_flop_horz_pitch.MsFlopHorzPitch"

sense_amp_array = "sense_amp_array"
sense_amp = "reram_sense_amp.ReRamSenseAmp"

br_reset_buffers = [1, 3.42, 11.7, 40]
bl_reset_buffers = [3.1, 9.65, 30]

logic_buffers_height = 4
run_optimizations = False
control_buffers_num_rows = 2
route_control_signals_left = True

# TODO:
# Buffer size optimizations
# column mux
# stacked wordline driver
# > 32 rows
# wordline vdd power
