"""Semantic execution choices and effective H3 labels; never authoring state."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class AssetRouting(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    use_frames: bool = Field(default=True, alias="useFrames", strict=True)
    use_embedded_audio: bool = Field(default=False, alias="useEmbeddedAudio", strict=True)
    use_audio: bool = Field(default=True, alias="useAudio", strict=True)


class GenerationRouting(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    assets: dict[str, AssetRouting] = Field(default_factory=dict, max_length=1000)


class EffectiveReference(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    asset_id: str = Field(alias="assetId")
    channel: Literal["image", "video_frames", "embedded_audio", "audio"]
    label: str


def enabled(channel, preference):
    return {"image": True, "video_frames": preference.use_frames,
            "embedded_audio": preference.use_embedded_audio, "audio": preference.use_audio}[channel]


def effective_references(workspace, routing=None):
    from miniconstruct.h3.builder import numbered_assets
    routing = routing or GenerationRouting()
    result, counts = [], {"Picture": 0, "Video": 0, "Audio": 0}
    for asset, _ in numbered_assets(workspace):
        preference = routing.assets.get(asset.id, AssetRouting())
        if asset.kind == "video" and preference.use_embedded_audio and not preference.use_frames:
            raise ValueError("Embedded video audio requires video frames to be enabled.")
        channels = {"image": [("image", "Picture")], "video": [("embedded_audio", "Audio"), ("video_frames", "Video")],
                    "audio": [("audio", "Audio")]}[asset.kind]
        for channel, kind in channels:
            if enabled(channel, preference):
                counts[kind] += 1
                result.append(EffectiveReference(assetId=asset.id, channel=channel, label=f"{kind} {counts[kind]}"))
    if counts["Audio"] > 3:
        raise ValueError("H3 supports at most 3 active Audio references, including embedded video soundtracks and standalone Audio.")
    return result


def prompt_projection(workspace, routing=None):
    """Transient prompt-writing view. Synthetic soundtrack entries never become assets or requirements."""
    from miniconstruct.models.workspace import AssetKind, ReferenceAsset
    refs = effective_references(workspace, routing)
    source = {asset.id: asset for asset in workspace.assets}
    assets = []
    for index, ref in enumerate(refs):
        asset = source[ref.asset_id]
        if ref.channel == "embedded_audio":
            import hashlib
            video_label = next(item.label for item in refs if item.asset_id == asset.id and item.channel == "video_frames")
            asset = ReferenceAsset.model_construct(id=f"soundtrack:{hashlib.sha256(asset.id.encode()).hexdigest()}", kind=AssetKind.AUDIO,
                filename=asset.filename, mime_type="audio/embedded", role="general_audio",
                duration_seconds=getattr(asset, "duration_seconds", None),
                options={"executionEmbeddedSoundtrack": True},
                notes=f"Enabled embedded soundtrack from <{video_label}> ({asset.filename}).")
        assets.append(asset.model_copy(update={"order": index}))
    return workspace.model_copy(update={"assets": assets})


def routing_from_references(workspace, references):
    if references is None:
        return GenerationRouting()
    routing = GenerationRouting(assets={asset.id: AssetRouting(useFrames=False, useAudio=False)
                                       for asset in workspace.assets})
    for ref in references:
        if ref.asset_id not in routing.assets:
            raise ValueError("Prompt reference map contains an asset outside this Generation.")
        preference = routing.assets[ref.asset_id]
        if ref.channel == "video_frames": preference.use_frames = True
        if ref.channel == "embedded_audio": preference.use_embedded_audio = True
        if ref.channel == "audio": preference.use_audio = True
    if effective_references(workspace, routing) != references:
        raise ValueError("Prompt reference map does not match effective H3 reference ordering.")
    return routing
