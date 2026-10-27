"""Offline media-source classification, without renderer discovery or execution.

The standard widget map is shared with existing imported-file diagnostics and
Send validation. Extra classifications and conservative unknown-loader rejection
are used only by the explicit pictureFreeI2va contract.
"""
import re


STANDARD_MEDIA_LOADERS = {"LoadImage": "image", "LoadVideo": "file", "LoadAudio": "audio"}

_MEDIA_LOADER_KINDS = {
    "LoadImage": "Picture", "LoadImageMask": "Picture", "LoadVideo": "Video", "LoadAudio": "Audio",
    # Exact Video Helper Suite registrations, not a wildcard over custom nodes.
    "VHS_LoadVideo": "Video", "VHS_LoadVideoPath": "Video",
    "VHS_LoadVideoFFmpeg": "Video", "VHS_LoadVideoFFmpegPath": "Video",
    "VHS_LoadImagePath": "Picture", "VHS_LoadImages": "Picture", "VHS_LoadImagesPath": "Picture",
    "VHS_LoadAudio": "Audio", "VHS_LoadAudioUpload": "Audio",
}
_NON_MEDIA_LOADERS = {
    "UNETLoader", "CLIPLoader", "DualCLIPLoader", "TripleCLIPLoader", "QuadrupleCLIPLoader", "VAELoader",
    "CheckpointLoader", "CheckpointLoaderSimple", "LoraLoader", "LoraLoaderModelOnly",
    "Power Lora Loader (rgthree)", "MiniMaxH3LoadLatent", "MiniMaxH3MotionContextLoadLatent",
}


def picture_free_source_error(node):
    """Known sources always fail, even empty, linked or disconnected loaders.

    Unknown loader/import/reader classes fail as unsupported, not as classified
    media. Arbitrary opaque custom code cannot be certified from API JSON.
    Generated media connections are graph links, not attached payload constants.
    """
    class_type = node["class_type"]
    if class_type in _MEDIA_LOADER_KINDS:
        return f"{class_type} is an imported {_MEDIA_LOADER_KINDS[class_type]} loader; remove it, including disconnected leftovers"
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", class_type).replace("_", " ").lower().split()
    if class_type not in _NON_MEDIA_LOADERS and any(
            word in {"load", "loader", "import", "importer", "read", "reader", "download", "downloader"}
            for word in words):
        return f"{class_type} has unrecognized loading semantics; unsupported by the picture-free contract"
    pending = [node["inputs"]]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if re.match(r"^\s*data:(?:image|video|audio)/", value, re.I):
                return f"{class_type} contains an attached media constant"
        elif isinstance(value, dict):
            if ({"mediaPayload", "media_payload"} & value.keys()
                    or {"sha256", "byteLength"} <= value.keys()
                    or {"waveform", "sample_rate"} <= value.keys()):
                return f"{class_type} contains an attached media payload"
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return None
