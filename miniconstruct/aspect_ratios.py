"""Offline ratio vocabulary and exact, closed-enum translation."""
import re

STANDARD_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9")


def ratio_token(value):
    token = re.match(r"^([0-9]+:[0-9]+)(?=$|\s|\()", value)
    return token[1] if token else None


def ratio_mappings(choices):
    if not choices or len(choices) != len(set(choices)) or any(
            not isinstance(choice, str) or not choice or len(choice) > 256 for choice in choices):
        raise ValueError("Enum choices must be unique bounded strings")
    result = {}
    for choice in choices:
        ratio = ratio_token(choice)
        if ratio:
            if ratio in result:
                raise ValueError(f"Ambiguous declared ratio {ratio}")
            result[ratio] = choice
    return result


def renderer_ratio(source, ratio, target=None):
    from miniconstruct.execution import InvalidBindings
    location = f" at {target}" if target else ""
    if source.enum_choices is not None:
        value = source.value_map.get(ratio, ratio_mappings(source.enum_choices).get(ratio, ratio))
        if value not in source.enum_choices:
            raise InvalidBindings([f'This Workflow Profile does not support H3 Aspect Ratio "{ratio}"{location} '
                                   'in its saved enum choices. Populate mappings from ComfyUI and review the Profile. '
                                   'Exact pixel dimensions require a separately declared width/height capability.'])
        requested, renderer = ratio_token(ratio), ratio_token(value)
        if requested and renderer and requested != renderer:
            raise InvalidBindings([f'Aspect Ratio semantic conflict{location}: H3 Setup requests "{ratio}", '
                                   f'but the saved mapping selects "{value}" (ratio {renderer}). '
                                   'Populate mappings from ComfyUI, explicitly resolve this conflict, and Save profile '
                                   'before Prepare. H3 Setup remains the aspect-ratio authority.'])
        return value
    if source.value_map and ratio in STANDARD_RATIOS and ratio not in source.value_map:
        raise InvalidBindings([f'Aspect Ratio mapping missing for "{ratio}"{location}. '
                               'Populate mappings from ComfyUI or add the exact accepted renderer value before Prepare.'])
    return source.value_map.get(ratio, ratio)
