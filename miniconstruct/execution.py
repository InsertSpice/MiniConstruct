"""Machine-local workflow configuration and offline binding contracts."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer

from miniconstruct.models.workspace import AssetKind, H3Mode

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
    source: Annotated[GeneratedPromptSource | DurationSource | ReferenceSource, Field(discriminator="kind")]
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
    target_seen = set()
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
        for deactivation in binding_deactivation_targets(binding):
            key = (deactivation.node_id, deactivation.input_name)
            value = graph.get(deactivation.node_id, {}).get("inputs", {}).get(deactivation.input_name)
            if key in target_seen:
                errors.append(f"{label}: deactivation target is declared more than once")
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


def optional_slot_errors(graph: dict, binding: Binding) -> list[str]:
    """Require every executable path from the loader to cross an exact target edge."""
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
    if allowed - declared:
        return ["Optional reference slot cannot be safely disabled: still reaches executable workflow output through an undeclared boundary path."]

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
                    "workflow output through an undeclared path."]
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


class ProfileEdit(NamedConfig):
    description: str = Field(default="", max_length=4000)
    compatible_modes: list[H3Mode] = Field(alias="compatibleModes", min_length=1, max_length=len(H3Mode))
    bindings: list[Binding] = Field(default_factory=list, max_length=1000)

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
        errors = binding_errors(self.workflow, self.bindings)
        if errors:
            raise ValueError("; ".join(errors))
        return self


class ExecutionConfig(StrictModel):
    schema_version: Literal[3] = Field(default=3, alias="schemaVersion")
    connections: list[Connection] = Field(default_factory=list)
    profiles: list[Profile] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_v1_and_v2(cls, value):
        if not isinstance(value, dict):
            return value
        source_version = value.get("schemaVersion", 2)
        value = {**value, "schemaVersion": 3}
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
            # Persist the v3 shape even when a caller is still holding a
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
                errors = binding_errors(graph, metadata.bindings)
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
    """The only Comfy network operation: bounded GET /system_stats, no redirects."""
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
