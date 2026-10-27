"""Explicit, read-only ComfyUI enum discovery for the saved Profile editor.

Never called by Prepare or Send. The user reviews the draft and saves normally.
"""
import asyncio
import json
from urllib.parse import quote

import httpx

from miniconstruct.execution import InvalidBindings, ExposedControl, control_target_value


async def enum_capabilities(profile, control_id, connection, *, transport=None):
    control = next((c for c in profile.exposed_controls if c.id == control_id), None)
    if control is None or control.type != 'enum' or control.target.path:
        raise InvalidBindings(['Choose a saved enum Workflow Control with a direct scalar input.'])
    current = control_target_value(profile.workflow, control.target)
    if type(current) not in (str, int, float, bool):
        raise InvalidBindings(['The saved enum target must be a scalar input.'])
    choices = await discover_enum(profile.workflow, control.target, connection, transport=transport)
    verified = ExposedControl.model_validate({**control.model_dump(), 'choices': choices})
    if type(verified.choices[0]) is not type(current):
        raise InvalidBindings(['Enum scalar type changed.'])
    return {'profileId': str(profile.id), 'controlId': control.id,
            'target': control.target.model_dump(by_alias=True),
            'classType': profile.workflow[control.target.node_id]['class_type'],
            'choices': choices, 'savedChoices': control.choices,
            'message': 'Verified from ComfyUI; review the draft choices, then click Save profile. Nothing has been saved.'}


async def discover_enum(workflow, target, connection, *, transport=None):
    try:
        current = control_target_value(workflow, target)
        class_type = workflow[target.node_id]['class_type']
    except (ValueError, KeyError) as exc:
        raise InvalidBindings(['Choose an existing direct scalar target.']) from exc
    error = 'Could not verify enum choices from ComfyUI. No Profile changes were saved.'
    try:
        async with asyncio.timeout(8):
            async with httpx.AsyncClient(transport=transport, timeout=8, trust_env=False, follow_redirects=False) as client:
                async with client.stream('GET', connection.base_url + '/object_info/' + quote(class_type, safe='')) as response:
                    if response.status_code != 200:
                        raise ValueError('HTTP response')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1024 * 1024:
                            raise ValueError('Metadata too large')
        payload = json.loads(raw)
        inputs = payload[class_type]['input']
        if not isinstance(inputs, dict) or any(not isinstance(inputs.get(section, {}), dict)
                                              for section in ('required', 'optional')):
            raise ValueError('Invalid input sections')
        declarations = [inputs[section][target.input_name] for section in ('required', 'optional')
                        if target.input_name in inputs.get(section, {})]
        if len(declarations) != 1:
            raise ValueError('Missing or ambiguous input')
        declaration = declarations[0]
        if not isinstance(declaration, list) or not declaration:
            raise ValueError('Invalid input declaration')
        # Legacy INPUT_TYPES and current V3 COMBO metadata are both supported.
        options = declaration[1] if len(declaration) > 1 else {}
        if not isinstance(options, dict) or options.get('forceInput') or options.get('multiselect'):
            raise ValueError('Not a scalar widget')
        choices = declaration[0] if isinstance(declaration[0], list) else (
            options.get('options') if declaration[0] == 'COMBO' else None)
        if not isinstance(choices, list):
            raise ValueError('No enum options')
        ExposedControl.model_validate({'id': 'discovery', 'label': 'Discovery', 'type': 'enum',
            'target': target.model_dump(by_alias=True), 'defaultValue': current, 'choices': choices})
    except (ValueError, TypeError, KeyError, IndexError, TimeoutError, httpx.HTTPError) as exc:
        raise InvalidBindings([error + ' Check the connection, exact node class/input, and that the saved default is still supported.']) from exc
    return choices


async def aspect_ratio_capabilities(profile, target, connection, *, transport=None):
    from miniconstruct.aspect_ratios import ratio_mappings
    if target.path:
        raise InvalidBindings(['Aspect Ratio discovery requires a direct scalar string input.'])
    choices = await discover_enum(profile.workflow, target, connection, transport=transport)
    try:
        mappings = ratio_mappings(choices)
        if not mappings:
            raise ValueError('No recognizable ratio tokens')
    except (ValueError, TypeError) as exc:
        raise InvalidBindings([f'Cannot populate Aspect Ratio mappings: {exc}. Nothing changed.']) from exc
    return {'profileId': str(profile.id), 'target': target.model_dump(by_alias=True),
            'classType': profile.workflow[target.node_id]['class_type'],
            'choices': choices, 'valueMap': mappings}
