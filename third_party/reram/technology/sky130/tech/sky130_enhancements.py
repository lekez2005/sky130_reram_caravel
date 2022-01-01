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

    poly_via_insts = list(sorted(poly_via_insts, key=lambda x: (x.lx(), x.by())))

    span = 0.5

    via_groups = []
    for via_inst in poly_via_insts:
        found = False
        for via_group in via_groups:
            (left_x, right_x, bot, top, existing_insts) = via_group
            span_left = left_x - span
            span_right = right_x + span
            span_top = top + span
            span_bot = bot - span

            if span_left <= via_inst.cx() <= span_right:
                if span_bot <= via_inst.cy() <= span_top:
                    existing_insts.append(via_inst)
                    via_group[0] = min(left_x, via_inst.lx())
                    via_group[1] = max(right_x, via_inst.rx())
                    via_group[2] = min(bot, via_inst.by())
                    via_group[3] = max(top, via_inst.uy())
                    found = True
                    break

        if not found:
            via_groups.append([via_inst.lx(), via_inst.rx(),
                               via_inst.by(), via_inst.uy(),
                               [via_inst]])

    for left, right, bot, top, _ in via_groups:
        x_offset = left - npc_enclose_poly - x_extension
        width = right + npc_enclose_poly + x_extension - x_offset
        y_offset = bot - npc_enclose_poly - y_extension
        height = top + npc_enclose_poly + y_extension - y_offset
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


combine = False


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
            if combine:
                x_sort = list(sorted(layer_rects, key=lambda x: x.lx()))
                y_sort = list(sorted(layer_rects, key=lambda x: x.by()))

                ll = vector(x_sort[0].lx(), y_sort[0].by())
                ur = vector(x_sort[-1].rx(), y_sort[-1].uy())
                obj.add_rect(layer, ll, width=ur.x - ll.x, height=ur.y - ll.y)
            else:
                for rect in layer_rects:
                    obj.add_rect(layer, rect.ll(), width=rect.rx() - rect.lx(),
                                 height=rect.uy() - rect.by())

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
