"""Conservative, offline contract for the stock H3 AV Save/Load topology.

No graph rewriting, renderer discovery, or evaluation of arbitrary dimensions.
An identity records model/codec declarations at execution time, not model bytes.
"""
import hashlib
import json

from miniconstruct.comfy_media import picture_free_source_error


def _shape(graph, cap):
    def mapped(semantic, class_type, input_name):
        mapping = cap.inputs[semantic]
        node = graph[mapping.node_id]
        if node["class_type"] != class_type or mapping.input_name != input_name:
            raise ValueError(f"{semantic} must map to {class_type} / {input_name}")
        return mapping.node_id

    extender = mapped("enable", "MiniMaxH3VideoExtender", "mode")
    save = mapped("save_key", "MiniMaxH3SaveLatent", "filename_prefix")
    load = mapped("load_key", "MiniMaxH3LoadLatent", "latent_path")
    for semantic, node, name in (("context_frames", extender, "context_length"),
                                  ("save_segment_index", save, "clip_index"),
                                  ("load_segment_index", load, "clip_index")):
        if (cap.inputs[semantic].node_id, cap.inputs[semantic].input_name) != (node, name):
            raise ValueError(f"{semantic} must map to {node} / {name}")
    links = [(r.node_id, r.input_name, r.expected_source_node_id, r.expected_output_index) for r in cap.source_links]
    if links != [(extender, "prev_latent", load, 0)]:
        raise ValueError("requires exactly the native Load output 0 → extender prev_latent source link")
    ext = graph[extender]["inputs"]
    h3 = ext["conditioning"][0]
    if graph[h3]["class_type"] != "MiniMaxH3ImageToVideo" or ext["conditioning"] != [h3, 0] or ext["latent"] != [h3, 1]:
        raise ValueError("requires stock H3 conditioning and empty AV latent")
    sampler, output = graph[save]["inputs"]["latent"]
    sample = graph[sampler]
    if output != 0 or sample["class_type"] != "SamplerCustomAdvanced" or sample["inputs"]["latent_image"] != [h3, 1]:
        raise ValueError("Save must receive the exact H3 sampler output")
    guider, output = sample["inputs"]["guider"]
    guide = graph[guider]
    if output != 0 or guide["class_type"] != "BasicGuider" or guide["inputs"]["conditioning"] != [extender, 0]:
        raise ValueError("sampler guider must consume the native extender conditioning")
    return h3, extender, save, load, guide["inputs"]["model"]


def state_identity(graph, cap):
    """Capture a portable, immutable same-model/codec/format declaration.

    Legacy records with no snapshot are deliberately ineligible for the new path.
    Node IDs and prompt/geometry/seed values do not define model compatibility.
    """
    try:
        h3, extender, _, load, model = _shape(graph, cap)
        visited = set()
        memo = {}
        def digest(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        def expand(value):
            if not isinstance(value, list):
                if isinstance(value, dict):
                    return {k: expand(v) for k, v in value.items()}
                return value
            if len(value) != 2 or not isinstance(value[0], str) or type(value[1]) is not int:
                raise ValueError("Unsupported model dependency")
            node_id, output = value
            if node_id in visited or len(visited) >= 256:
                raise ValueError("Cyclic or excessive model dependencies")
            if (node_id, output) in memo:
                return memo[node_id, output]
            if len(memo) >= 4096:
                raise ValueError("Excessive model dependencies")
            visited.add(node_id)
            node = graph[node_id]
            result = {"class": node["class_type"], "output": output, "inputs": expand(node["inputs"])}
            visited.remove(node_id)
            # Hash each dependency once: shared model DAGs must not expand into
            # exponential nested JSON, and node IDs are not compatibility facts.
            memo[node_id, output] = {"graphOutput": digest(result)}
            return memo[node_id, output]
        inputs = graph[h3]["inputs"]
        roots = {"model": model, "clip": inputs["clip"], "vae": inputs["vae"],
                 "extenderVae": graph[extender]["inputs"]["vae"]}
        for name in ("vae", "audio_vae"):
            for node, label in ((extender, "extender"), (load, "load")):
                if name in graph[node]["inputs"]:
                    roots[label + name] = graph[node]["inputs"][name]
        value = {"version": 1, "format": cap.compatibility_key, "models": expand(roots)}
        return digest(value)
    except (KeyError, ValueError, TypeError, IndexError, RecursionError):
        return None


def picture_free_errors(graph, bindings, cap):
    if cap is None or cap.picture_free_i2va is None:
        return []
    errors = []
    try:
        h3, extender, _, _, _ = _shape(graph, cap)
        if h3 != cap.picture_free_i2va.conditioning_node_id:
            errors.append("conditioningNodeId must identify the native extender's H3 node")
        inputs = graph[h3]["inputs"]
        for name in ("width", "height"):
            if type(inputs.get(name)) is not int or not 32 <= inputs[name] <= 16384 or inputs[name] % 32:
                errors.append(f"{h3} / {name} requires an independent integer canvas dimension divisible by 32; Picture/loader-fed dimensions are unsupported")
        if any(b.source.kind == "reference_asset" for b in bindings):
            errors.append("successor Profile must have no media reference bindings")
        if not any(b.source.kind == "generated_prompt" and b.node_id == h3 and b.input_name == "prompt" for b in bindings):
            errors.append("generated prompt must map to the declared H3 prompt input")
        # Narrow opt-in: exactly one H3 node, no imported image or video anchor,
        # including unused loaders. Unknown topologies need a separate contract.
        for node_id, node in graph.items():
            source_error = picture_free_source_error(node)
            if source_error:
                errors.append(f"{node_id}: {source_error}")
            if node["class_type"] == "MiniMaxH3ImageToVideo" and node_id != h3:
                errors.append("successor Profile must have exactly one H3 conditioning node")
            if any(name in node["inputs"] for name in ("first_frame", "last_frame", "prev_frames", "prev_audio", "image")):
                errors.append(f"{node_id}: imported image/endpoint media inputs are forbidden in a picture-free successor")
        if cap.inputs["enable"].enabled_value != "always_extend":
            errors.append("native enable enabledValue must be always_extend to prevent renderer fresh-clip fallback")
        if graph[extender]["inputs"].get("anchor_mode") != "head":
            errors.append("only the verified head-overlap extender contract is supported")
        if state_identity(graph, cap) is None:
            errors.append("cannot establish the model/codec state compatibility identity")
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        errors.append(f"invalid stock H3 native topology: {exc}")
    return ["Picture-free I2VA: " + error for error in errors]
