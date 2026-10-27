"""Narrow runtime compatibility for explicitly declared legacy aspect-ratio controls."""
import re

from miniconstruct.execution import InvalidBindings, control_target_value, control_errors


def legacy_aspect_ratio_control(profile):
    modern = [binding for binding in profile.bindings if binding.source.kind == "aspect_ratio"]
    candidates = [control for control in profile.exposed_controls if control.id == "aspect_ratio"]
    if modern:
        # Explicit modern authority wins. A second explicitly declared ratio target
        # is not safely known to be independent; do not queue conflicting dimensions.
        for control in candidates:
            if control.target.input_name in {"aspect_ratio", "aspectRatio"} and (
                    control.target.node_id, control.target.input_name) not in {
                        (binding.node_id, binding.input_name) for binding in modern}:
                raise InvalidBindings(["Modern Aspect Ratio binding and legacy aspect_ratio Workflow Control "
                                       "target different inputs. Keep one explicit Aspect Ratio authority before Prepare."])
        return None
    if not candidates:
        return None
    if len(candidates) != 1:
        raise InvalidBindings(["Multiple legacy aspect_ratio Workflow Controls are ambiguous; use one explicit authority."])
    control = candidates[0]
    target = control.target
    error = "Legacy aspect_ratio Workflow Control must declare one canonical scalar string enum target with no conflicting authority."
    if target.input_name not in {"aspect_ratio", "aspectRatio"} or target.path or control.type != "enum" or not control.choices or any(
            type(choice) is not str for choice in control.choices):
        raise InvalidBindings([error])
    try:
        value = control_target_value(profile.workflow, target)
    except ValueError as exc:
        raise InvalidBindings([error]) from exc
    if type(value) is not str or control_errors(profile.workflow, profile.exposed_controls, profile.bindings, profile.native_continuation):
        raise InvalidBindings([error])
    if sum((item.target.node_id, item.target.input_name) == (target.node_id, target.input_name)
           for item in profile.exposed_controls) != 1:
        raise InvalidBindings([error])
    return control


def legacy_renderer_value(control, ratio, profile=None):
    # The complete leading ratio token must match, not a substring or a fuzzy label.
    # Non-numeric values (including auto) require an exact declared enum option.
    if re.fullmatch(r"[0-9]+:[0-9]+", ratio):
        pattern = re.compile(r"^" + re.escape(ratio) + r"(?=$|\s|\()").match
        matches = [choice for choice in control.choices if pattern(choice)]
    else:
        matches = [choice for choice in control.choices if choice == ratio]
    if len(matches) != 1:
        reason = "missing" if not matches else "ambiguous"
        recognized = [choice for choice in control.choices if re.match(r"^[0-9]+:[0-9]+(?=$|\s|\()", choice)]
        owner = f'Profile "{profile.name}" ({profile.id}), ' if profile is not None else ''
        raise InvalidBindings([f'Cannot map H3 Setup "{ratio}" to legacy Aspect Ratio enum '
                               f'{control.target.node_id} / {control.target.input_name}: {reason} declared choice. '
                               f'{owner}control "{control.label}" ({control.id}). '
                               f'Recognized saved Profile choices: {recognized or "none"}. '
                               f'Ratio {ratio} is {reason} in the saved Profile declaration; this does not establish ComfyUI support. '
                               'Open this Profile in Execution settings, choose the Enum capability connection, '
                               'click Sync choices from ComfyUI beside this control, review, then Save profile and Prepare again. '
                               'Unsupported or ambiguous ratios remain blocked; an explicit modern aspect_ratio binding is also supported.'])
    return matches[0]
