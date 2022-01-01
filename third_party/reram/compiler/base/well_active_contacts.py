import math

from base import utils
from base.design import design, ACTIVE
from tech import drc


def calculate_num_contacts(design_obj: design, tx_width, return_sample=False,
                           layer_stack=None, contact_spacing=None):
    """
    Calculates the possible number of source/drain contacts in a finger.
    """
    from base import contact
    contact_spacing = contact_spacing or design_obj.contact_spacing
    num_contacts = int(math.ceil(tx_width / (design_obj.contact_width + contact_spacing)))
    layer_stack = layer_stack or contact.well.layer_stack

    def create_array():
        return contact.contact(layer_stack=layer_stack,
                               dimensions=[1, num_contacts],
                               implant_type=None,
                               well_type=None)
    while num_contacts > 1:
        contact_array = create_array()
        if (contact_array.first_layer_height < tx_width and
                contact_array.second_layer_height < tx_width):
            if return_sample:
                return contact_array
            break
        num_contacts -= 1

    if return_sample and num_contacts == 0:
        num_contacts = 1
    if num_contacts == 1 and return_sample:
        return create_array()
    return num_contacts


def get_max_contact(layer_stack, height):
    """Get contact that can fit the given height"""
    from base.contact import contact
    num_contacts = 1
    prev_contact = None
    while True:
        sample_contact = contact(layer_stack, dimensions=[1, num_contacts])
        if num_contacts == 1:
            prev_contact = sample_contact
        if sample_contact.height > height:
            return prev_contact
        prev_contact = sample_contact
        num_contacts += 1


def calculate_contact_width(design_obj: design, width, well_contact_active_height):
    body_contact = calculate_num_contacts(design_obj, width - design_obj.contact_pitch,
                                          return_sample=True)

    contact_extent = body_contact.first_layer_height

    min_active_area = drc.get("minarea_cont_active_thin", design_obj.get_min_area(ACTIVE))
    min_active_width = utils.ceil(min_active_area / well_contact_active_height)
    active_width = max(contact_extent, min_active_width)

    # prevent minimum spacing drc
    active_width = max(active_width, width)
    return active_width, body_contact
