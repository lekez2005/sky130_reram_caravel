import debug
from base import contact
from base.contact import m1m2
from base.design import design, POLY, ACTIVE, METAL2
from base.vector import vector
from base.well_active_contacts import calculate_num_contacts
from globals import OPTS
from pgates.pgate import pgate
from tech import drc


def get_vias(obj: design):
    return [x for x in enumerate(obj.insts) if isinstance(x[1].mod, contact.contact)]


def add_stdc(obj: design):
    """Add stdc around active rects"""
    active_rects = obj.get_layer_shapes(ACTIVE)
    for rect in active_rects:
        obj.add_rect("stdc", rect.ll(), width=rect.width, height=rect.height)


def seal_poly_vias(obj: design):
    """Add npc around poly contacts"""
    poly_via_insts = [x[1] for x in get_vias(obj) if x[1].mod.layer_stack[0] == POLY]
    if not poly_via_insts:
        return
    debug.info(2, f"Sealing Poly vias in module {obj.name}")

    sample_via = poly_via_insts[0].mod
    x_extension = 0.5 * (sample_via.first_layer_width - sample_via.width)
    y_extension = 0.5 * (sample_via.first_layer_height - sample_via.height)

    npc_enclose_poly = drc.get("npc_enclose_poly")

    for via_inst in poly_via_insts:
        x_offset = via_inst.lx() - npc_enclose_poly - x_extension
        width = via_inst.rx() + npc_enclose_poly + x_extension - x_offset
        y_offset = via_inst.by() - npc_enclose_poly - y_extension
        height = via_inst.uy() + npc_enclose_poly + y_extension - y_offset
        obj.add_rect("npc", vector(x_offset, y_offset), width=width, height=height)


def enhance_pgate(obj: design):
    if not OPTS.enhance_pgate_pins or not isinstance(obj, pgate) or True:
        return
    pin_names = ["vdd", "gnd"]

    contact_space = m1m2.contact_pitch - m1m2.contact_width
    for pin_name in pin_names:
        pin = obj.get_pin(pin_name)
        num_vias = calculate_num_contacts(obj, pin.width(), layer_stack=m1m2.layer_stack,
                                          contact_spacing=contact_space)
        obj.add_contact_center(m1m2.layer_stack, offset=pin.center(), rotate=90,
                               size=[1, num_vias])
        obj.add_rect_center(METAL2, offset=pin.center(), width=pin.width(),
                            height=max(pin.height(), obj.m2_width))


def flatten_vias(obj: design):
    """Flatten vias by moving via shapes from via instance to top level
       Also combine multiple rects into encompassing rect
    """
    debug.info(2, f"Flattening vias in module {obj.name}")
    all_via_inst = get_vias(obj)
    for _, via_inst in all_via_inst:
        layers = list(via_inst.mod.layer_stack)
        if via_inst.mod.implant_type:
            layers.append(f"{via_inst.mod.implant_type}implant")
        for layer in layers:
            layer_rects = via_inst.get_layer_shapes(layer, recursive=False)

            x_sort = list(sorted(layer_rects, key=lambda x: x.lx()))
            y_sort = list(sorted(layer_rects, key=lambda x: x.by()))

            ll = vector(x_sort[0].lx(), y_sort[0].by())
            ur = vector(x_sort[-1].rx(), y_sort[-1].uy())
            obj.add_rect(layer, ll, width=ur.x - ll.x, height=ur.y - ll.y)

    all_via_index = [x[0] for x in all_via_inst]
    obj.insts = [inst for inst_index, inst in enumerate(obj.insts)
                 if inst_index not in all_via_index]
    obj.conns = [conn for conn_index, conn in enumerate(obj.conns)
                 if conn_index not in all_via_index]


def enhance_module(obj: design):
    debug.info(2, f"Enhancing module {obj.name}")
    # add stdc and seal poly before flattening vias
    add_stdc(obj)
    seal_poly_vias(obj)
    enhance_pgate(obj)
    flatten_vias(obj)
