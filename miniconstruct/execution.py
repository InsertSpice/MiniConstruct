"""Machine-local workflow configuration and offline binding contracts."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
from threading import RLock
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator, model_serializer

from miniconstruct.models.workspace import AssetKind, H3Mode
from miniconstruct.native_continuation_models import NativeCapability

MAX_WORKFLOW_BYTES = 2 * 1024 * 1024
MAX_STATS_BYTES = 64 * 1024
TEST_TIMEOUT_SECONDS = 5
MAX_OPTIONAL_GRAPH_NODES = 4096
MAX_OPTIONAL_GRAPH_EDGES = 16384
OPTIONAL_BOUNDARY_ERROR = ("Optional reference slot cannot be safely disabled: MiniConstruct could not determine "
                           "a safe optional-reference boundary for this branch. "
                           "Make the reference Required or simplify/configure the workflow.")
_LOCK = RLock()


class ExecutionError(Exception):
    pass


class ConfigNotFound(ExecutionError):
    pass


class InvalidBindings(ExecutionError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def normalize_base_url(value: str) -> str:
    value = value.strip()
    if any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("Base URL must not contain whitespace or backslashes")
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError()
        if parts.username is not None or parts.password is not None:
            raise ValueError("Embedded username/password is unsupported")
        if "?" in value or "#" in value:
            raise ValueError("Base URL must not contain a query or fragment")
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError()
        if parts.netloc.endswith(":"):
            raise ValueError()
        host = parts.hostname
        if ":" in host:
            ipaddress.IPv6Address(host)
        else:
            ascii_host = host.encode("idna").decode("ascii")
            if len(ascii_host) > 253 or not all(
                re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in ascii_host.rstrip(".").split(".")
            ):
                raise ValueError()
        if re.search(r"%(?![0-9a-fA-F]{2})", parts.path):
            raise ValueError()
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    except (ValueError, UnicodeError) as exc:
        if str(exc) in ("Embedded username/password is unsupported", "Base URL must not contain a query or fragment"):
            raise ValueError(str(exc)) from exc
        raise ValueError("Enter a valid HTTP(S) base URL with a valid host and port") from exc


class NamedConfig(StrictModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value.strip()


class ConnectionWrite(NamedConfig):
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=2048)
    local_input_directory: str | None = Field(default=None, alias="localInputDirectory", max_length=4096)

    @field_validator("local_input_directory")
    @classmethod
    def clean_input_directory(cls, value):
        if value is None or not value.strip():
            return None
        value = value.strip()
        if any(ord(c) < 32 for c in value) or not Path(value).is_absolute():
            raise ValueError("Local input directory must be an absolute path on the MiniConstruct backend")
        return value

    @field_validator("base_url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return normalize_base_url(value)


class Connection(ConnectionWrite):
    id: UUID = Field(default_factory=uuid4)


class GeneratedPromptSource(StrictModel):
    kind: Literal["generated_prompt"]


class DurationSource(StrictModel):
    kind: Literal["duration"]


class AspectRatioSource(StrictModel):
    kind: Literal["aspect_ratio"]
    value_map: dict[StrictStr, StrictStr] = Field(default_factory=dict, alias="valueMap", max_length=16)
    enum_choices: list[StrictStr] | None = Field(default=None, alias="enumChoices", min_length=1, max_length=100)

    @model_serializer(mode="wrap")
    def optional_enum_declaration(self, handler):
        value = handler(self)
        if self.enum_choices is None:
            value.pop("enumChoices", None)
            value.pop("enum_choices", None)
        return value

    @field_validator("enum_choices")
    @classmethod
    def unique_enum_choices(cls, value):
        if value is not None:
            from miniconstruct.aspect_ratios import ratio_mappings
            ratio_mappings(value)
        return value

    @field_validator("value_map")
    @classmethod
    def bounded_value_map(cls, value):
        if any(not key or len(key) > 40 or not target or len(target) > 256 for key, target in value.items()):
            raise ValueError("aspect_ratio valueMap keys must be 1–40 characters and values 1–256 characters")
        return value


class RendererSeedSource(StrictModel):
    kind: Literal["renderer_seed"]


class ReferenceSource(StrictModel):
    kind: Literal["reference_asset"]
    asset_kind: AssetKind = Field(alias="assetKind")
    ordinal: int = Field(ge=1, strict=True)


class ControlledConsumer(StrictModel):
    node_id: str = Field(alias="nodeId", min_length=1)
    input_name: str = Field(alias="inputName", min_length=1)
    output_index: int = Field(default=0, alias="outputIndex", ge=0, strict=True)


class DeactivationTarget(StrictModel):
    """An exact input edge owned by an optional semantic reference."""

    node_id: str = Field(alias="nodeId", min_length=1)
    input_name: str = Field(alias="inputName", min_length=1)
    expected_source_node_id: str = Field(alias="expectedSourceNodeId", min_length=1)
    expected_output_index: int = Field(alias="expectedOutputIndex", ge=0, strict=True)


class ReferenceRoute(DeactivationTarget):
    channel: Literal["image", "video_frames", "embedded_audio", "audio"] | None = None


class Binding(StrictModel):
    source: Annotated[GeneratedPromptSource | DurationSource | AspectRatioSource | RendererSeedSource | ReferenceSource, Field(discriminator="kind")]
    node_id: str = Field(alias="nodeId", min_length=1)
    input_name: str = Field(alias="inputName", min_length=1)
    presence: Literal["optional", "required"] = "optional"
    routes: list[ReferenceRoute] = Field(default_factory=list, max_length=1000)
    deactivation_targets: list[DeactivationTarget] = Field(default_factory=list, alias="deactivationTargets", max_length=1000)
    # Read-only compatibility input for schema v2. It is normalized to
    # deactivation_targets and is never serialized in the v3 contract.
    controlled_consumers: list[ControlledConsumer] = Field(default_factory=list, alias="controlledConsumers",
                                                            max_length=1000, exclude=True)
    legacy_contract: bool = Field(default=False, exclude=True, repr=False)

    @model_validator(mode="before")
    @classmethod
    def identify_legacy_contract(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        has_legacy = "controlledConsumers" in value or "controlled_consumers" in value
        has_targets = "deactivationTargets" in value or "deactivation_targets" in value
        if has_legacy and not has_targets:
            value["legacy_contract"] = True
        return value

    @model_validator(mode="after")
    def reference_options_only(self):
        if self.routes and (self.deactivation_targets or self.controlled_consumers):
            raise ValueError("Use routes or legacy deactivation targets, not both")
        if self.routes:
            if self.source.kind != "reference_asset":
                raise ValueError("Routes apply only to reference assets")
            channels = {"image": {"image"}, "video": {"video_frames", "embedded_audio"}, "audio": {"audio"}}
            if any(route.channel is not None and route.channel not in channels[self.source.asset_kind] for route in self.routes):
                raise ValueError("Route channel does not match the reference kind")
        if self.source.kind != "reference_asset" and (self.controlled_consumers or self.deactivation_targets
                                                        or self.presence != "optional"):
            raise ValueError("Reference presence and deactivation targets apply only to reference assets")
        if self.source.kind == "reference_asset" and self.legacy_contract:
            self.deactivation_targets = [DeactivationTarget(
                nodeId=item.node_id,
                inputName=item.input_name,
                expectedSourceNodeId=self.node_id,
                expectedOutputIndex=item.output_index,
            ) for item in self.controlled_consumers]
        return self

    @model_serializer(mode="wrap")
    def serialize_reference_options(self, handler):
        result = handler(self)
        if self.source.kind != "reference_asset":
            for key in ("routes", "presence", "deactivationTargets", "controlledConsumers", "controlled_consumers", "legacy_contract"):
                result.pop(key, None)
        elif self.legacy_contract:
            # Preserve the exact v2 wire shape for callers that supplied the
            # legacy field. The in-memory model still exposes the normalized
            # targets and the next new-format edit can emit v3 targets.
            result.pop("deactivationTargets", None)
            result["controlledConsumers"] = [item.model_dump(by_alias=True) for item in self.controlled_consumers]
        else:
            result.pop("controlledConsumers", None)
            result.pop("controlled_consumers", None)
            result.pop("legacy_contract", None)
        if self.routes:
            result.pop("deactivationTargets", None)
        elif self.deactivation_targets and not self.legacy_contract:
            result["routes"] = [route.model_dump(by_alias=True) for route in binding_routes(self)]
            result.pop("deactivationTargets", None)
        else:
            result.pop("routes", None)
        return result


def binding_deactivation_targets(binding: Binding) -> list[DeactivationTarget]:
    """Return the canonical target list, honoring an in-memory v2 edit."""
    if binding.routes:
        return binding.routes
    if binding.legacy_contract:
        return [DeactivationTarget(nodeId=item.node_id, inputName=item.input_name,
                                   expectedSourceNodeId=binding.node_id,
                                   expectedOutputIndex=item.output_index)
                for item in binding.controlled_consumers]
    return binding.deactivation_targets


def binding_routes(binding: Binding) -> list[ReferenceRoute]:
    if binding.routes:
        return binding.routes
    # A legacy Video edge has no evidence identifying frames versus soundtrack.
    channel = {"image": "image", "audio": "audio", "video": None}[binding.source.asset_kind]
    return [ReferenceRoute(**target.model_dump(), channel=channel) for target in binding_deactivation_targets(binding)]


def route_order_errors(bindings: list[Binding]) -> list[str]:
    """Check recognized H3 socket positions against declared semantic capacity, without class-name authority."""
    errors = []
    prefixes = {"ref_images.ref_image_": "image", "ref_videos.ref_video_": "video_frames",
                "ref_video_audios.ref_video_audio_": "embedded_audio", "ref_audios.ref_audio_": "audio"}
    for binding in bindings:
        if binding.source.kind != "reference_asset": continue
        for route in binding_routes(binding):
            for prefix, channel in prefixes.items():
                if route.input_name.startswith(prefix) and route.input_name[len(prefix):].isdigit():
                    if route.channel is not None and (route.channel != channel or int(route.input_name[len(prefix):]) != binding.source.ordinal - 1):
                        errors.append("H3 route channel/socket order must match the binding's semantic ordinal. Correct the mapping once in the Workflow Profile.")
    return errors


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not supported")
        result[key] = value
    return result


def check_graph(graph: Any) -> dict[str, Any]:
    if not isinstance(graph, dict) or not graph:
        raise ValueError("Workflow must be a non-empty API-format object keyed by node IDs")
    if isinstance(graph.get("nodes"), list) or isinstance(graph.get("links"), list):
        raise ValueError("This is an editor workflow. Export an API-format workflow from ComfyUI and import that JSON")
    pending = [(graph, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 64:
            raise ValueError("Workflow JSON exceeds the 64-level nesting limit")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    for node_id, node in graph.items():
        if not isinstance(node_id, str) or not node_id or not isinstance(node, dict):
            raise ValueError("Each API workflow node must be an object with a non-empty string ID")
        if not isinstance(node.get("class_type"), str) or not node["class_type"].strip() or not isinstance(node.get("inputs"), dict):
            raise ValueError("Each API workflow node requires class_type text and an inputs object")
    try:
        if len(canonical_json(graph)) > MAX_WORKFLOW_BYTES:
            raise ValueError("Workflow exceeds the 2 MiB size limit")
    except (RecursionError, UnicodeError, OverflowError) as exc:
        raise ValueError("Workflow JSON is too deeply nested or contains invalid text/numbers") from exc
    return graph


def parse_workflow(value: str) -> dict[str, Any]:
    try:
        if len(value.encode("utf-8")) > MAX_WORKFLOW_BYTES:
            raise ValueError("Workflow exceeds the 2 MiB size limit")
        graph = json.loads(value, object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Workflow requires finite JSON numbers")))
        return check_graph(graph)
    except (json.JSONDecodeError, RecursionError, UnicodeError) as exc:
        raise ValueError("Invalid workflow JSON; import a valid API-format workflow") from exc


def workflow_fingerprint(graph: dict) -> str:
    return hashlib.sha256(canonical_json(graph)).hexdigest()


def is_graph_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and type(value[1]) is int and value[1] >= 0


def binding_errors(graph: dict, bindings: list[Binding]) -> list[str]:
    errors, seen = [], set()
    if sum(binding.source.kind == "renderer_seed" for binding in bindings) > 1:
        errors.append("Workflow Profile may map only one renderer_seed input.")
    target_seen = set()
    semantic_targets = {(binding.node_id, binding.input_name) for binding in bindings}
    for index, binding in enumerate(bindings, 1):
        target = (binding.node_id, binding.input_name)
        label = f"Binding {index} ({binding.node_id} / {binding.input_name})"
        if target in seen:
            errors.append(f"{label}: duplicate target")
        seen.add(target)
        if binding.node_id not in graph:
            errors.append(f"{label}: target node does not exist")
        elif binding.input_name not in graph[binding.node_id]["inputs"]:
            errors.append(f"{label}: target input does not exist")
        else:
            value = graph[binding.node_id]["inputs"][binding.input_name]
            if isinstance(value, (list, dict)):
                kind = "graph link" if is_graph_link(value) else "structured input"
                errors.append(f"{label}: {kind} cannot be bound; choose a scalar input")
            elif binding.source.kind == "aspect_ratio" and not isinstance(value, str):
                errors.append(f"{label}: aspect_ratio must target a scalar string input")
            elif binding.source.kind == "renderer_seed" and (type(value) is not int or value < 0 or value > 18446744073709551615):
                errors.append(f"{label}: renderer_seed must target a non-negative integer seed input")
        for deactivation in binding_deactivation_targets(binding):
            key = (deactivation.node_id, deactivation.input_name)
            value = graph.get(deactivation.node_id, {}).get("inputs", {}).get(deactivation.input_name)
            if key in target_seen:
                errors.append(f"{label}: deactivation target is declared more than once")
            if key in semantic_targets:
                errors.append(f"{label}: deactivation target conflicts with another semantic binding target")
            target_seen.add(key)
            expected = [deactivation.expected_source_node_id, deactivation.expected_output_index]
            if deactivation.expected_source_node_id not in graph:
                errors.append(f"{label}: deactivation target expected source node does not exist")
            if value != expected or deactivation.node_id == deactivation.expected_source_node_id:
                errors.append(f"{label}: deactivation target must match its expected source node and output index")
    return errors


def _graph_outgoing(graph: dict) -> dict[str, list[tuple[str, str, int]]]:
    outgoing: dict[str, list[tuple[str, str, int]]] = {node_id: [] for node_id in graph}
    for destination_id, node in graph.items():
        for input_name, value in node["inputs"].items():
            if is_graph_link(value) and value[0] in graph:
                outgoing[value[0]].append((destination_id, input_name, value[1]))
    return outgoing


def _downstream_nodes(graph: dict, loader_id: str, outgoing: dict[str, list[tuple[str, str, int]]]):
    """Bounded full reachability used only to distinguish private vs outside inputs."""
    visited = {loader_id}
    queue = [loader_id]
    while queue:
        source_id = queue.pop(0)
        if len(visited) > MAX_OPTIONAL_GRAPH_NODES:
            return None
        for destination_id, _input_name, _output_index in outgoing.get(source_id, []):
            if destination_id not in visited:
                visited.add(destination_id)
                queue.append(destination_id)
    return visited


def reference_boundary_targets(graph: dict, loader_id: str) -> tuple[set[tuple[str, str, str, int]], str | None]:
    """Return the first safe reference-branch input edges, never every edge.

    A boundary destination has an input dependency outside the loader's full
    downstream component. Traversal stops at every such merge boundary; a
    terminal graph sink without that independent dependency is not sufficient.
    """
    outgoing = _graph_outgoing(graph)
    edge_count = sum(len(edges) for edges in outgoing.values())
    if loader_id not in graph or len(graph) > MAX_OPTIONAL_GRAPH_NODES or edge_count > MAX_OPTIONAL_GRAPH_EDGES:
        return set(), OPTIONAL_BOUNDARY_ERROR
    downstream = _downstream_nodes(graph, loader_id, outgoing)
    if downstream is None:
        return set(), OPTIONAL_BOUNDARY_ERROR

    def is_boundary(destination_id: str) -> bool:
        destination = graph[destination_id]
        for value in destination["inputs"].values():
            if is_graph_link(value):
                if value[0] not in downstream:
                    return True
        return False

    allowed: set[tuple[str, str, str, int]] = set()
    visited = {loader_id}
    queue = [loader_id]
    while queue:
        source_id = queue.pop(0)
        for destination_id, input_name, output_index in outgoing.get(source_id, []):
            edge = (source_id, destination_id, input_name, output_index)
            if is_boundary(destination_id):
                allowed.add(edge)
                continue
            if destination_id not in visited:
                visited.add(destination_id)
                queue.append(destination_id)
    return (allowed, None) if allowed else (set(), OPTIONAL_BOUNDARY_ERROR)


def optional_slot_errors(graph: dict, binding: Binding, *, require_deactivation: bool = True) -> list[str]:
    """Validate route ownership; removable slots must cut every executable path.

    Always-active Required Pictures still need exact, reachable reference
    boundaries. They do not need to disconnect other dependencies such as size.
    """
    targets = binding_deactivation_targets(binding)
    if not targets:
        return [OPTIONAL_BOUNDARY_ERROR]
    edge_count = sum(sum(1 for value in node["inputs"].values() if is_graph_link(value))
                     for node in graph.values())
    if len(graph) > MAX_OPTIONAL_GRAPH_NODES or edge_count > MAX_OPTIONAL_GRAPH_EDGES:
        return ["Optional reference graph analysis exceeded its safety bound; simplify the downstream branch "
                "or make the slot required."]

    outgoing = _graph_outgoing(graph)
    allowed, boundary_error = reference_boundary_targets(graph, binding.node_id)
    if boundary_error:
        return [boundary_error]
    declared = {(item.expected_source_node_id, item.node_id, item.input_name, item.expected_output_index)
                for item in targets}
    if not declared.issubset(allowed):
        return ["Optional reference slot contains a deactivation target outside the safe reference-branch boundary."]
    # Allowed edges were discovered from this loader within the safety bounds.
    # Keep that ownership check even when the Picture cannot be deactivated.
    if not require_deactivation:
        return []
    uncovered = sorted(allowed - declared)
    if uncovered:
        details = "; ".join(f"{destination[:120]} / {name[:120]} <- {source[:120]} output {output}"
                            for source, destination, name, output in uncovered[:8])
        if len(uncovered) > 8:
            details += f"; and {len(uncovered) - 8} more inputs"
        guidance = "Keep this slot Required if these inputs are needed. Only declare routes the destination supports omitting."
        if any(name in {"width", "height"} and graph[source]["class_type"] == "GetImageSize"
               for source, _destination, name, _output in uncovered):
            guidance += (" These dimensions still depend on the Picture; a Picture-free workflow needs independent "
                         "width and height in ComfyUI, followed by a new API export. Do not deactivate required dimensions.")
        return ["Optional reference slot cannot be safely disabled: still reaches executable workflow output "
                f"through undeclared boundary inputs: {details}. {guidance}"]

    # Discoverability and the safety walk share one deterministic, cycle-safe
    # graph traversal. A target that is not downstream of the mapped loader is
    # not an ownership declaration for this semantic slot.
    reachable_targets: set[tuple[str, str, str, int]] = set()
    visited = {binding.node_id}
    queue = [binding.node_id]
    while queue:
        source_id = queue.pop(0)
        for destination_id, input_name, output_index in outgoing.get(source_id, []):
            edge = (source_id, destination_id, input_name, output_index)
            if edge in declared:
                reachable_targets.add(edge)
            if destination_id not in visited:
                visited.add(destination_id)
                queue.append(destination_id)
    if len(reachable_targets) != len(declared):
        return ["Optional reference slot has a deactivation destination that is not reachable from its mapped loader."]

    # API-format graphs do not carry an explicit output-node list. For this
    # offline contract, a reachable node with no outgoing graph links is the
    # conservative executable-terminal approximation. Compute it before cuts;
    # an adapter that becomes a leaf only because its terminal links were cut
    # is an orphan, not a new output that must be pruned.
    original_terminals = {node_id for node_id in visited if not outgoing.get(node_id)
                          and node_id != binding.node_id}

    # Removing only declared input links must leave no path to an original
    # terminal node. Intermediate nodes are intentionally retained.
    cut_edges = declared
    visited = {binding.node_id}
    queue = [binding.node_id]
    while queue:
        source_id = queue.pop(0)
        if source_id in original_terminals:
            return [f"Optional reference slot cannot be safely disabled: Optional "
                    f"{binding.source.asset_kind.title()} {binding.source.ordinal} still reaches executable "
                    f"workflow output through an undeclared path to node {source_id[:120]} "
                    f"({graph[source_id]['class_type'][:120]}). Keep the slot Required or revise this branch "
                    "in ComfyUI and re-export it; MiniConstruct will not prune it."]
        remaining = [edge for edge in outgoing.get(source_id, [])
                     if (source_id, edge[0], edge[1], edge[2]) not in cut_edges]
        for destination_id, input_name, output_index in remaining:
            if destination_id not in visited:
                visited.add(destination_id)
                queue.append(destination_id)
    return []


def profile_readiness_errors(graph: dict, bindings: list[Binding]) -> list[str]:
    errors = binding_errors(graph, bindings) + route_order_errors(bindings)
    for binding in bindings:
        if binding.source.kind != "reference_asset":
            continue
        routes = binding_routes(binding)
        if binding.source.asset_kind in {"video", "audio"} and not routes:
            kind = "Video" if binding.source.asset_kind == "video" else "Audio"
            errors.append(f"{binding.node_id} / {binding.input_name}: Configure/classify this Workflow Profile's {kind} route capacity once before preparation.")
        elif any(route.channel is None for route in routes):
            errors.append(f"{binding.node_id}: Classify preserved reference routes once in this Workflow Profile.")
        if binding.presence == "optional":
            errors.extend(f"{binding.node_id} / {binding.input_name}: {error}"
                          for error in optional_slot_errors(graph, binding))
    return errors


def native_mapping_errors(graph, bindings, capability):
    if capability is None:
        return []
    from miniconstruct.i2va_contract import picture_free_errors
    errors = picture_free_errors(graph, bindings, capability)
    seen = {(b.node_id, b.input_name) for b in bindings}
    seen.update((r.node_id, r.input_name) for b in bindings if b.source.kind == "reference_asset"
                for r in binding_routes(b))
    for semantic, mapping in capability.inputs.items():
        target = (mapping.node_id, mapping.input_name)
        inputs = graph.get(mapping.node_id, {}).get("inputs", {})
        if mapping.input_name not in inputs or isinstance(inputs.get(mapping.input_name), (list, dict)):
            errors.append(f"Native {semantic}: mapped node/input must exist and be scalar")
        if target in seen:
            errors.append(f"Native {semantic}: mapped input conflicts with another binding/route")
        seen.add(target)
    source_targets = set()
    for index, link in enumerate(capability.source_links, 1):
        target = (link.node_id, link.input_name)
        label = f"Native source link {index} ({link.node_id} / {link.input_name})"
        value = graph.get(link.node_id, {}).get("inputs", {}).get(link.input_name)
        expected = [link.expected_source_node_id, link.expected_output_index]
        if target in source_targets:
            errors.append(f"{label}: duplicate target")
        source_targets.add(target)
        if target in seen:
            errors.append(f"{label}: target conflicts with a scalar native mapping or binding/route")
        if link.expected_source_node_id not in graph:
            errors.append(f"{label}: expected source node does not exist")
        if link.node_id == link.expected_source_node_id or value != expected:
            errors.append(f"{label}: target must match its expected source node and output index")
    return errors


class ControlTarget(StrictModel):
    node_id: str = Field(alias="nodeId", min_length=1, max_length=80)
    input_name: str = Field(alias="inputName", min_length=1, max_length=160)
    path: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("path")
    @classmethod
    def bounded_keys(cls, value):
        if any(not key or len(key) > 160 for key in value):
            raise ValueError("Control path keys must be 1–160 characters")
        return value


class ExposedControl(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    label: str = Field(min_length=1, max_length=160)
    target: ControlTarget
    type: Literal["boolean", "integer", "float", "enum"]
    default_value: Any = Field(alias="defaultValue")
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: list[Any] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def declaration(self):
        if not self.label.strip():
            raise ValueError("Control label must not be blank")
        if self.type in {"integer", "float"}:
            for bound in (self.minimum, self.maximum, self.step):
                if bound is not None and not math.isfinite(bound):
                    raise ValueError("Numeric control limits must be finite")
            if self.step is not None and self.step <= 0:
                raise ValueError("Control step must be positive")
            if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
                raise ValueError("Control minimum exceeds maximum")
            if self.type == "integer" and any(bound is not None and not bound.is_integer() for bound in (self.minimum, self.maximum, self.step)):
                raise ValueError("Integer control limits must be integers")
        elif any(bound is not None for bound in (self.minimum, self.maximum, self.step)):
            raise ValueError("Only numeric controls may have limits")
        if self.type == "enum":
            if not self.choices or any(type(v) not in (str, int, float, bool) or
                                       (type(v) is str and len(v) > 512) or
                                       (type(v) is float and not math.isfinite(v)) for v in self.choices):
                raise ValueError("Enum requires finite scalar choices")
            if len({(type(v), v) for v in self.choices}) != len(self.choices) or len({type(v) for v in self.choices}) != 1:
                raise ValueError("Enum choices must be unique and have one scalar type")
        elif self.choices:
            raise ValueError("Choices apply only to enum controls")
        control_value(self, self.default_value)
        return self


def control_value(control: ExposedControl, value: Any):
    kind = control.type
    finite_number = False
    if type(value) in (int, float):
        try:
            finite_number = math.isfinite(value)
        except OverflowError:
            finite_number = False
    valid = (type(value) is bool if kind == "boolean" else
             type(value) is int if kind == "integer" else
             type(value) in (int, float) and finite_number if kind == "float" else
             type(value) is type(control.choices[0]) and value in control.choices)
    if not valid:
        raise ValueError(f"{control.id}: value does not match {kind} control")
    if kind in {"integer", "float"} and ((control.minimum is not None and value < control.minimum)
                                          or (control.maximum is not None and value > control.maximum)):
        raise ValueError(f"{control.id}: value is outside configured bounds")
    return float(value) if kind == "float" else value


def control_target_value(graph: dict, target: ControlTarget):
    inputs = graph.get(target.node_id, {}).get("inputs", {})
    if target.input_name not in inputs:
        raise ValueError("target node or input does not exist")
    value = inputs[target.input_name]
    for key in target.path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError("structured dictionary path does not exist")
        value = value[key]
    if is_graph_link(value):
        raise ValueError("graph link cannot be a control target")
    if type(value) not in (str, int, float, bool):
        raise ValueError("control target must be a scalar leaf")
    return value


def control_errors(graph, controls, bindings, native):
    errors, ids, targets = [], set(), set()
    owned = {(b.node_id, b.input_name) for b in bindings}
    owned.update((r.node_id, r.input_name) for b in bindings if b.source.kind == "reference_asset" for r in binding_routes(b))
    if native:
        owned.update((m.node_id, m.input_name) for m in native.inputs.values())
        owned.update((m.node_id, m.input_name) for m in native.source_links)
    for control in controls:
        target = control.target
        key = (target.node_id, target.input_name, tuple(target.path))
        if control.id in ids:
            errors.append(f"Control {control.id}: duplicate ID")
        if key in targets:
            errors.append(f"Control {control.id}: duplicate target leaf")
        if (target.node_id, target.input_name) in owned:
            errors.append(f"Control {control.id}: target is owned by a semantic binding or native mapping")
        ids.add(control.id); targets.add(key)
        try:
            leaf = control_target_value(graph, target)
            if control.type != "enum" and (type(leaf) is not type(control.default_value)
                                           and not (control.type == "float" and type(leaf) in (int, float))):
                raise ValueError("target leaf type does not match control")
            if control.type == "enum" and type(leaf) is not type(control.choices[0]):
                raise ValueError("target leaf type does not match enum choices")
        except ValueError as exc:
            errors.append(f"Control {control.id}: {exc}")
    return errors


class WorkflowControlPreset(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    values: dict[str, Any] = Field(max_length=128)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("Preset name must not be blank")
        return value


class ProfileEdit(NamedConfig):
    native_continuation: NativeCapability | None = Field(default=None, alias="nativeContinuation")
    description: str = Field(default="", max_length=4000)
    compatible_modes: list[H3Mode] = Field(alias="compatibleModes", min_length=1, max_length=len(H3Mode))
    bindings: list[Binding] = Field(default_factory=list, max_length=1000)
    exposed_controls: list[ExposedControl] = Field(default_factory=list, alias="exposedControls", max_length=128)
    workflow_control_presets: list[WorkflowControlPreset] = Field(default_factory=list, alias="workflowControlPresets", max_length=64)

    @model_validator(mode="after")
    def valid_presets(self):
        if (self.native_continuation and self.native_continuation.picture_free_i2va
                and self.compatible_modes != [H3Mode.I2VA]):
            raise ValueError("Picture-free successor Profiles must declare only I2VA compatibility")
        declared = {control.id: control for control in self.exposed_controls}
        if not declared and self.workflow_control_presets:
            raise ValueError("Workflow Control presets require exposed controls")
        names = set()
        for preset in self.workflow_control_presets:
            folded = preset.name.strip().casefold()
            if folded in names:
                raise ValueError(f"Duplicate Workflow Control preset name: {preset.name}")
            names.add(folded)
            if set(preset.values) != set(declared):
                raise ValueError(f"Preset {preset.name} must contain exactly every exposed control ID")
            for key, value in preset.values.items():
                control_value(declared[key], value)
        return self

    @field_validator("compatible_modes")
    @classmethod
    def distinct_modes(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Compatible modes must be distinct")
        return value


class WorkflowImport(StrictModel):
    workflow_json: str = Field(alias="workflowJson", max_length=MAX_WORKFLOW_BYTES)

    @field_validator("workflow_json")
    @classmethod
    def valid_workflow(cls, value):
        parse_workflow(value)
        return value


class ProfileCreate(ProfileEdit, WorkflowImport):
    pass


class ProfileUpdate(ProfileEdit):
    workflow_json: str | None = Field(default=None, alias="workflowJson", max_length=MAX_WORKFLOW_BYTES)

    @field_validator("workflow_json")
    @classmethod
    def valid_optional_workflow(cls, value):
        if value is not None:
            parse_workflow(value)
        return value


class Profile(ProfileEdit):
    id: UUID = Field(default_factory=uuid4)
    workflow: dict[str, Any]
    fingerprint: str

    @model_validator(mode="after")
    def validate_source(self):
        check_graph(self.workflow)
        if self.fingerprint != workflow_fingerprint(self.workflow):
            raise ValueError("Workflow fingerprint does not match the source graph")
        errors = (binding_errors(self.workflow, self.bindings) + native_mapping_errors(self.workflow, self.bindings, self.native_continuation)
                  + control_errors(self.workflow, self.exposed_controls, self.bindings, self.native_continuation))
        if errors:
            raise ValueError("; ".join(errors))
        return self


class ExecutionConfig(StrictModel):
    schema_version: Literal[7] = Field(default=7, alias="schemaVersion")
    connections: list[Connection] = Field(default_factory=list)
    profiles: list[Profile] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_execution_config(cls, value):
        if not isinstance(value, dict):
            return value
        source_version = value.get("schemaVersion", 2)
        if source_version not in (1, 2, 3, 4, 5, 6, 7):
            raise ValueError("Unsupported execution configuration version")
        value = {**value, "schemaVersion": 7}
        # v1 had no optional-reference ownership contract. Do not invent
        # direct or transitive destinations while upgrading it.
        if source_version == 1:
            for profile in value.get("profiles", []):
                for binding in profile.get("bindings", []):
                    if isinstance(binding, dict):
                        binding.pop("controlledConsumers", None)
                        binding.pop("controlled_consumers", None)
                        binding.pop("deactivationTargets", None)
                        binding.pop("deactivation_targets", None)
        # v2 controlledConsumers are converted by Binding's lossless
        # compatibility validator into direct deactivationTargets.
        return value

    @model_validator(mode="after")
    def unique_ids(self):
        for records in (self.connections, self.profiles):
            if len({record.id for record in records}) != len(records):
                raise ValueError("Duplicate configuration IDs")
        return self


def default_execution_root() -> Path:
    configured = os.environ.get("MINICONSTRUCT_EXECUTION_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "MiniConstruct" / "Execution"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "miniconstruct" / "execution"


class ExecutionStore:
    """Reload under a process-wide lock; publish only after atomic replacement."""

    def __init__(self, root: Path | None = None):
        self.path = (Path(root) if root is not None else default_execution_root()) / "execution.json"

    def _read(self) -> ExecutionConfig:
        try:
            return ExecutionConfig.model_validate_json(self.path.read_bytes())
        except FileNotFoundError:
            return ExecutionConfig()
        except (OSError, ValueError) as exc:
            raise ExecutionError("Execution configuration could not be read; the existing file was preserved") from exc

    def _write(self, config: ExecutionConfig):
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            # Persist the current file with v3 reference mappings even when a caller holds a
            # v2-compatible in-memory model. This keeps legacy edits usable
            # without allowing the old wire concept to survive a mutation.
            payload = config.model_dump(mode="json", by_alias=True)
            for profile in payload.get("profiles", []):
                for binding in profile.get("bindings", []):
                    legacy = binding.pop("controlledConsumers", None)
                    if legacy is not None and "deactivationTargets" not in binding:
                        binding["deactivationTargets"] = [{
                            "nodeId": item["nodeId"],
                            "inputName": item["inputName"],
                            "expectedSourceNodeId": binding["nodeId"],
                            "expectedOutputIndex": item.get("outputIndex", 0),
                        } for item in legacy]
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise ExecutionError("Execution configuration could not be saved; previous configuration was preserved") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def list(self, collection: Literal["connections", "profiles"]):
        with _LOCK:
            return getattr(self._read(), collection)

    def get(self, collection, record_id: UUID):
        with _LOCK:
            for record in self.list(collection):
                if record.id == record_id:
                    return record
            raise ConfigNotFound("Connection or Workflow Profile was not found")

    def save(self, collection, data, record_id: UUID | None = None):
        with _LOCK:
            config = self._read()
            records = getattr(config, collection)
            old = next((item for item in records if item.id == record_id), None)
            if record_id is not None and old is None:
                raise ConfigNotFound("Connection or Workflow Profile was not found")
            if collection == "connections":
                record = Connection(**data.model_dump(), id=record_id or uuid4())
            else:
                imported = getattr(data, "workflow_json", None)
                graph = parse_workflow(imported) if imported is not None else old.workflow
                metadata = data if isinstance(data, ProfileEdit) else old
                errors = (binding_errors(graph, metadata.bindings) + native_mapping_errors(graph, metadata.bindings, metadata.native_continuation)
                          + control_errors(graph, metadata.exposed_controls, metadata.bindings, metadata.native_continuation))
                if errors:
                    raise InvalidBindings(errors)
                record = Profile(**metadata.model_dump(by_alias=True, include=set(ProfileEdit.model_fields)),
                                 workflow=graph, fingerprint=workflow_fingerprint(graph), id=record_id or uuid4())
            if old is None:
                records.append(record)
            else:
                records[records.index(old)] = record
            self._write(config)
            return record

    def duplicate_profile_draft(self, record_id: UUID) -> ProfileCreate:
        """Read one coherent snapshot; return reusable configuration without identity.

        Serialize the authoritative graph here, before JavaScript can round large
        integers. Creation still goes through the ordinary validated save path.
        """
        with _LOCK:
            profiles = self._read().profiles
            source = next((item for item in profiles if item.id == record_id), None)
            if source is None:
                raise ConfigNotFound("Workflow Profile was not found")
            names = {item.name.casefold() for item in profiles}
            number = 1
            while True:
                suffix = " (Copy)" if number == 1 else f" (Copy {number})"
                name = source.name[:160 - len(suffix)] + suffix
                if name.casefold() not in names:
                    break
                number += 1
            return ProfileCreate(**{
                **source.model_dump(by_alias=True, include=set(ProfileEdit.model_fields)),
                "name": name, "workflowJson": json.dumps(source.workflow, ensure_ascii=False, separators=(",", ":")),
            })

    def delete(self, collection, record_id: UUID):
        with _LOCK:
            config = self._read()
            records = getattr(config, collection)
            old = next((item for item in records if item.id == record_id), None)
            if old is None:
                raise ConfigNotFound("Connection or Workflow Profile was not found")
            records.remove(old)
            self._write(config)


async def test_connection(connection: Connection, *, transport=None) -> dict:
    """Perform the explicit Connection test with a bounded GET /system_stats and no redirects."""
    try:
        async with asyncio.timeout(TEST_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(transport=transport, timeout=TEST_TIMEOUT_SECONDS,
                                         follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", connection.base_url + "/system_stats") as response:
                    if response.status_code != 200:
                        return {"status": "invalid_response", "message": f"Server returned HTTP {response.status_code}; expected ComfyUI system stats"}
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_STATS_BYTES:
                            return {"status": "invalid_response", "message": "System stats response exceeds 64 KiB"}
                    payload = json.loads(raw)
                    if not isinstance(payload, dict) or not isinstance(payload.get("system"), dict) or not isinstance(payload.get("devices"), list) or not isinstance(payload["system"].get("os"), str):
                        return {"status": "invalid_response", "message": "Response is not standard ComfyUI system stats"}
                    return {"status": "connected", "message": "Connected to ComfyUI. No workflow was executed"}
    except (TimeoutError, httpx.TimeoutException):
        return {"status": "timeout", "message": "Connection test timed out after 5 seconds"}
    except (ValueError, UnicodeError, RecursionError, httpx.DecodingError, httpx.RemoteProtocolError):
        return {"status": "invalid_response", "message": "Server returned invalid system stats"}
    except httpx.RequestError:
        return {"status": "unreachable", "message": "Could not reach ComfyUI; check the URL and that the server is running"}
