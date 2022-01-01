import os
import sys

TECHNOLOGY = "sky130"

##########################
# Sky130 paths

PDK_ROOT = os.path.abspath(os.environ.get("PDK_ROOT"))
PDK_DIR = os.path.join(PDK_ROOT, "sky130A", "libs.tech")

os.environ["SPICE_MODEL_DIR"] = os.path.join(PDK_DIR, "ngspice")
os.environ["SPICE_MODEL_HSPICE"] = os.path.join(PDK_ROOT, "sky130A", "libs.ref", "hspice", "models")

magic_dir = os.path.join(PDK_DIR, "magic", "current")
os.environ["MAGIC_RC"] = os.path.join(magic_dir, "sky130A.magicrc")
tmp_dir = os.path.join(os.environ.get("SCRATCH", "/"), "tmp")
os.environ["MGC_TMPDIR"] = os.path.join(tmp_dir, "magic")

netgen_dir = os.path.join(PDK_DIR, "netgen")
os.environ["NETGEN_RC"] = os.path.join(netgen_dir, "setup.tcl")

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                                "modules")))

export_library_name = "generated"
work_dir = os.environ.get("WORK_DIR_SKY130", None)
if work_dir:
    os.environ["MAGIC_GDS_EXPORT_DIR"] = os.path.join(work_dir, export_library_name)
    os.environ["MAGIC_WORK_DIR"] = work_dir
