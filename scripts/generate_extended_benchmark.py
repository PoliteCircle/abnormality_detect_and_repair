from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import random
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from chapter7.bpmn import BPMN_NS, load_process_models
from chapter7.diagnosis import build_repair_scopes, find_minimal_abnormal_structures
from chapter7.logs import load_global_log, project_global_trace
from chapter7.model import ProcessModel, Trace, node_to_expression
from chapter7.patterns import accepts_trace
from chapter7.repairs import generate_repair_candidates


DEFAULT_SEED = 20260809
GENERATED_LOG_START = 100
BASE_CASES = (
    "qingdao_port",
    "qingdao_port_simple",
    "concrete_casting",
    "airport_pass",
    "company_client",
    "self_service_restaurant",
    "quote_order",
    "credit_scoring",
)
SYNTHETIC_SPECS = (
    ("synthetic_scale_05p_012m", 5, 12),
    ("synthetic_scale_07p_020m", 7, 20),
    ("synthetic_scale_09p_032m", 9, 32),
    ("synthetic_scale_12p_048m", 12, 48),
    ("synthetic_scale_16p_072m", 16, 72),
    ("synthetic_scale_20p_096m", 20, 96),
)


@dataclass(frozen=True)
class MessageSpec:
    name: str
    partner_index: int


@dataclass(frozen=True)
class BlockSpec:
    kind: str
    messages: tuple[MessageSpec, ...]


@dataclass(frozen=True)
class DatasetEvaluation:
    detected: bool
    strict_repair_success: bool
    affected_participants: tuple[str, ...]
    minimal_abnormal_structures: dict[str, tuple[str, ...]]
    strict_rule_ids: dict[str, tuple[int, ...]]


@dataclass(frozen=True)
class ManifestEntry:
    case: str
    model_origin: str
    bpmn_file: str
    log_file: str
    seed: int
    participant_count: int
    message_flow_count: int
    normal_trace_count: int
    abnormal_trace_count: int
    anomaly_type: str
    injection_ground_truth: str
    injected_messages: tuple[str, ...]
    expected_participants: tuple[str, ...]
    detected_participants: tuple[str, ...]
    detected_minas: dict[str, tuple[str, ...]]
    strict_rule_ids: dict[str, tuple[int, ...]]
    detected: bool
    strict_repair_success: bool
    bpmn_sha256: str
    log_sha256: str


def _qname(local: str) -> str:
    return f"{{{BPMN_NS}}}{local}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_token(token: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", token)


def _add_sequence_flow(process: ET.Element, process_id: str, index: int, source: str, target: str) -> int:
    ET.SubElement(
        process,
        _qname("sequenceFlow"),
        {
            "id": f"Flow_{process_id}_{index:04d}",
            "sourceRef": source,
            "targetRef": target,
        },
    )
    return index + 1


def _make_blocks(case_index: int, participant_count: int, message_count: int) -> tuple[BlockSpec, ...]:
    if participant_count < 5:
        raise ValueError("synthetic collaboration requires at least five participants")
    if message_count < 8 or message_count % 2:
        raise ValueError("synthetic message count must be an even number of at least eight")

    regular_receivers = tuple(range(1, participant_count - 2))
    choice_receivers = (participant_count - 2, participant_count - 1)
    regular_count = message_count - 2
    message_number = 1
    receiver_cursor = 0
    blocks: list[BlockSpec] = []

    def next_regular_message() -> MessageSpec:
        nonlocal message_number, receiver_cursor
        result = MessageSpec(
            f"S{case_index:02d}_M{message_number:03d}",
            regular_receivers[receiver_cursor % len(regular_receivers)],
        )
        message_number += 1
        receiver_cursor += 1
        return result

    remaining = regular_count
    while remaining >= 4:
        blocks.append(BlockSpec("leaf", (next_regular_message(),)))
        blocks.append(BlockSpec("leaf", (next_regular_message(),)))
        blocks.append(BlockSpec("parallel", (next_regular_message(), next_regular_message())))
        remaining -= 4
    while remaining:
        blocks.append(BlockSpec("leaf", (next_regular_message(),)))
        remaining -= 1

    choice_messages = (
        MessageSpec(f"S{case_index:02d}_M{message_number:03d}", choice_receivers[0]),
        MessageSpec(f"S{case_index:02d}_M{message_number + 1:03d}", choice_receivers[1]),
    )
    blocks.append(BlockSpec("choice", choice_messages))
    return tuple(blocks)


def _build_synthetic_bpmn(
    case_name: str,
    case_index: int,
    participant_count: int,
    message_count: int,
    destination: Path,
) -> tuple[BlockSpec, ...]:
    blocks = _make_blocks(case_index, participant_count, message_count)
    ET.register_namespace("bpmn", BPMN_NS)
    root = ET.Element(
        _qname("definitions"),
        {
            "id": f"Definitions_{_safe_token(case_name)}",
            "targetNamespace": "https://example.org/pais/synthetic-benchmark",
        },
    )
    collaboration = ET.SubElement(
        root,
        _qname("collaboration"),
        {"id": f"Collaboration_{_safe_token(case_name)}"},
    )
    for participant_index in range(participant_count):
        participant_name = "Coordinator" if participant_index == 0 else f"Partner_{participant_index:02d}"
        ET.SubElement(
            collaboration,
            _qname("participant"),
            {
                "id": f"Participant_{participant_index:02d}",
                "name": participant_name,
                "processRef": f"Process_{participant_index:02d}",
            },
        )

    coordinator_endpoints: dict[str, str] = {}
    partner_endpoints: dict[str, str] = {}
    coordinator = ET.SubElement(
        root,
        _qname("process"),
        {"id": "Process_00", "isExecutable": "false"},
    )
    start_id = "Start_Process_00"
    ET.SubElement(coordinator, _qname("startEvent"), {"id": start_id})
    current = start_id
    flow_index = 1
    for block_index, block in enumerate(blocks, start=1):
        if block.kind == "leaf":
            message = block.messages[0]
            task_id = f"Task_Send_{message.name}"
            # Generic tasks are used deliberately.  PM4Py's BPMN-to-process-tree
            # converter handles them consistently across versions, while the
            # collaboration messageFlow still supplies the send/receive direction.
            ET.SubElement(coordinator, _qname("task"), {"id": task_id, "name": message.name})
            coordinator_endpoints[message.name] = task_id
            flow_index = _add_sequence_flow(coordinator, "00", flow_index, current, task_id)
            current = task_id
            continue

        gateway_tag = "parallelGateway" if block.kind == "parallel" else "exclusiveGateway"
        split_id = f"Gateway_{block.kind}_split_{block_index:03d}"
        join_id = f"Gateway_{block.kind}_join_{block_index:03d}"
        ET.SubElement(
            coordinator,
            _qname(gateway_tag),
            {"id": split_id, "gatewayDirection": "Diverging"},
        )
        ET.SubElement(
            coordinator,
            _qname(gateway_tag),
            {"id": join_id, "gatewayDirection": "Converging"},
        )
        flow_index = _add_sequence_flow(coordinator, "00", flow_index, current, split_id)
        for message in block.messages:
            direction = "Receive" if block.kind == "choice" else "Send"
            task_id = f"Task_{direction}_{message.name}"
            ET.SubElement(coordinator, _qname("task"), {"id": task_id, "name": message.name})
            coordinator_endpoints[message.name] = task_id
            flow_index = _add_sequence_flow(coordinator, "00", flow_index, split_id, task_id)
            flow_index = _add_sequence_flow(coordinator, "00", flow_index, task_id, join_id)
        current = join_id

    # A visible internal activity after the final XOR keeps PM4Py's WF-net
    # reducer from emitting the malformed tail ``->(X(...), tau)``.  It is not
    # connected to a messageFlow and is therefore simplified to tau by the
    # Chapter 7 message-tree converter.
    finalize_id = "Task_Internal_Finalize"
    ET.SubElement(coordinator, _qname("task"), {"id": finalize_id, "name": "Internal_Finalize"})
    flow_index = _add_sequence_flow(coordinator, "00", flow_index, current, finalize_id)
    current = finalize_id
    end_id = "End_Process_00"
    ET.SubElement(coordinator, _qname("endEvent"), {"id": end_id})
    _add_sequence_flow(coordinator, "00", flow_index, current, end_id)

    messages_in_order = [message for block in blocks for message in block.messages]
    block_kind_by_message = {
        message.name: block.kind for block in blocks for message in block.messages
    }
    for participant_index in range(1, participant_count):
        process_id = f"Process_{participant_index:02d}"
        process = ET.SubElement(
            root,
            _qname("process"),
            {"id": process_id, "isExecutable": "false"},
        )
        start_id = f"Start_{process_id}"
        ET.SubElement(process, _qname("startEvent"), {"id": start_id})
        current = start_id
        flow_index = 1
        assigned = [message for message in messages_in_order if message.partner_index == participant_index]
        choice_messages = [
            message for message in assigned if block_kind_by_message[message.name] == "choice"
        ]
        regular_messages = [
            message for message in assigned if block_kind_by_message[message.name] != "choice"
        ]
        if choice_messages:
            if regular_messages or len(choice_messages) != 1:
                raise ValueError("choice senders must be dedicated single-message participants")
            message = choice_messages[0]
            split_id = f"Gateway_optional_split_{participant_index:02d}"
            join_id = f"Gateway_optional_join_{participant_index:02d}"
            ET.SubElement(
                process,
                _qname("exclusiveGateway"),
                {"id": split_id, "gatewayDirection": "Diverging"},
            )
            ET.SubElement(
                process,
                _qname("exclusiveGateway"),
                {"id": join_id, "gatewayDirection": "Converging"},
            )
            flow_index = _add_sequence_flow(
                process, f"{participant_index:02d}", flow_index, current, split_id
            )
            task_id = f"Task_Send_{message.name}"
            ET.SubElement(process, _qname("task"), {"id": task_id, "name": message.name})
            partner_endpoints[message.name] = task_id
            flow_index = _add_sequence_flow(
                process, f"{participant_index:02d}", flow_index, split_id, task_id
            )
            flow_index = _add_sequence_flow(
                process, f"{participant_index:02d}", flow_index, task_id, join_id
            )
            internal_id = f"Task_Internal_Skip_{participant_index:02d}"
            ET.SubElement(process, _qname("task"), {"id": internal_id, "name": "Internal_Skip"})
            flow_index = _add_sequence_flow(
                process, f"{participant_index:02d}", flow_index, split_id, internal_id
            )
            flow_index = _add_sequence_flow(
                process, f"{participant_index:02d}", flow_index, internal_id, join_id
            )
            current = join_id
        else:
            for message in regular_messages:
                task_id = f"Task_Receive_{message.name}"
                ET.SubElement(process, _qname("task"), {"id": task_id, "name": message.name})
                partner_endpoints[message.name] = task_id
                flow_index = _add_sequence_flow(
                    process, f"{participant_index:02d}", flow_index, current, task_id
                )
                current = task_id
        end_id = f"End_{process_id}"
        ET.SubElement(process, _qname("endEvent"), {"id": end_id})
        _add_sequence_flow(process, f"{participant_index:02d}", flow_index, current, end_id)

    for flow_index, message in enumerate(messages_in_order, start=1):
        if block_kind_by_message[message.name] == "choice":
            source_ref = partner_endpoints[message.name]
            target_ref = coordinator_endpoints[message.name]
        else:
            source_ref = coordinator_endpoints[message.name]
            target_ref = partner_endpoints[message.name]
        ET.SubElement(
            collaboration,
            _qname("messageFlow"),
            {
                "id": f"MessageFlow_{flow_index:03d}",
                "name": message.name,
                "sourceRef": source_ref,
                "targetRef": target_ref,
            },
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
    return blocks


def _normal_trace(blocks: tuple[BlockSpec, ...], rng: random.Random) -> Trace:
    result: list[str] = []
    for block in blocks:
        names = [message.name for message in block.messages]
        if block.kind == "leaf":
            result.extend(names)
        elif block.kind == "parallel":
            if rng.randrange(2):
                names.reverse()
            result.extend(names)
        elif block.kind == "choice":
            result.append(names[rng.randrange(len(names))])
        else:
            raise ValueError(f"unknown block kind: {block.kind}")
    return tuple(result)


def _synthetic_normals(blocks: tuple[BlockSpec, ...], rng: random.Random, target: int = 12) -> tuple[Trace, ...]:
    normals: list[Trace] = []
    seen: set[Trace] = set()
    for _ in range(5000):
        trace = _normal_trace(blocks, rng)
        if trace not in seen:
            seen.add(trace)
            normals.append(trace)
        if len(normals) >= target:
            break
    if len(normals) < 2:
        raise RuntimeError("synthetic model did not produce enough distinct normal traces")
    return tuple(normals)


def _evaluate_dataset(
    models: tuple[ProcessModel, ...] | list[ProcessModel],
    normal_traces: tuple[Trace, ...],
    abnormal_trace: Trace,
    *,
    behavior_limit: int = 20_000,
) -> DatasetEvaluation:
    affected: list[str] = []
    minas: dict[str, tuple[str, ...]] = {}
    strict_rules: dict[str, tuple[int, ...]] = {}
    all_scopes_strict = True

    for model in models:
        projected_normals = tuple(
            project_global_trace(trace, model.sends, model.receives) for trace in normal_traces
        )
        if not all(accepts_trace(model.tree, trace, model.sends, model.receives) for trace in projected_normals):
            raise ValueError(f"generated normal trace is rejected by {model.participant_name}")

        projected_abnormal = project_global_trace(abnormal_trace, model.sends, model.receives)
        if accepts_trace(model.tree, projected_abnormal, model.sends, model.receives):
            continue

        affected.append(model.participant_name)
        diagnosis = find_minimal_abnormal_structures(
            model.tree,
            projected_abnormal,
            source_index=1,
            line_number=1,
            global_trace=abnormal_trace,
        )
        minimal = tuple(node_to_expression(item.node) for item in diagnosis.minimal_structures)
        minas[model.participant_name] = minimal
        scopes = build_repair_scopes(model.tree, diagnosis.minimal_structures)
        participant_rules: list[int] = []
        if not scopes:
            all_scopes_strict = False
        for scope in scopes:
            candidates = generate_repair_candidates(
                model,
                scope,
                normal_traces=projected_normals,
                abnormal_traces=(projected_abnormal,),
                behavior_limit=behavior_limit,
            )
            valid = tuple(
                candidate
                for candidate in candidates
                if candidate.definition_717_satisfied is True
                and candidate.normal_after_pass == candidate.normal_total
                and candidate.abnormal_after_pass == candidate.abnormal_total
            )
            if not valid:
                all_scopes_strict = False
            else:
                participant_rules.append(valid[0].rule_id)
        strict_rules[model.participant_name] = tuple(participant_rules)

    detected = bool(affected)
    return DatasetEvaluation(
        detected=detected,
        strict_repair_success=detected and all_scopes_strict,
        affected_participants=tuple(affected),
        minimal_abnormal_structures=minas,
        strict_rule_ids=strict_rules,
    )


def _write_log(
    path: Path,
    *,
    seed: int,
    model_origin: str,
    anomaly_type: str,
    ground_truth: str,
    normal_traces: tuple[Trace, ...],
    abnormal_trace: Trace,
) -> None:
    lines = [
        "# generated extended benchmark",
        f"# seed: {seed}",
        f"# model_origin: {model_origin}",
        f"# anomaly_type: {anomaly_type}",
        f"# injection_ground_truth: {ground_truth}",
        "# normal traces",
    ]
    lines.extend(",".join(trace) for trace in normal_traces)
    lines.extend(("", "# abnormal trace", ",".join(abnormal_trace), ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _synthetic_anomaly_candidates(
    blocks: tuple[BlockSpec, ...],
    normal_traces: tuple[Trace, ...],
    rng: random.Random,
):
    regular_messages = [
        message.name
        for block in blocks
        if block.kind != "choice"
        for message in block.messages
    ]
    swap_pairs = [
        (left.messages[0].name, right.messages[0].name)
        for left, right in zip(blocks, blocks[1:])
        if left.kind == right.kind == "leaf"
    ]
    choice = next(block for block in blocks if block.kind == "choice")
    choice_names = tuple(message.name for message in choice.messages)

    candidates: list[tuple[str, str, tuple[str, ...], tuple[str, ...], Trace]] = []
    for normal in normal_traces:
        for message in regular_messages:
            if message not in normal:
                continue
            abnormal = list(normal)
            abnormal.remove(message)
            candidates.append(
                (
                    "missing_message",
                    f"delete message {message}",
                    (message,),
                    ("Coordinator",),
                    tuple(abnormal),
                )
            )
        for first, second in swap_pairs:
            first_index = normal.index(first)
            second_index = normal.index(second)
            abnormal = list(normal)
            abnormal[first_index], abnormal[second_index] = abnormal[second_index], abnormal[first_index]
            candidates.append(
                (
                    "sequence_inversion",
                    f"swap sequential messages {first} and {second}",
                    (first, second),
                    ("Coordinator",),
                    tuple(abnormal),
                )
            )
        selected = next(name for name in choice_names if name in normal)
        missing = next(name for name in choice_names if name != selected)
        selected_index = normal.index(selected)
        abnormal = list(normal)
        abnormal.insert(selected_index + 1, missing)
        candidates.append(
            (
                "choice_conflict",
                f"execute mutually exclusive messages {selected} and {missing} together",
                (selected, missing),
                ("Coordinator",),
                tuple(abnormal),
            )
        )
    rng.shuffle(candidates)
    return candidates


def _all_original_normal_traces(case_dir: Path, models: list[ProcessModel]) -> tuple[Trace, ...]:
    result: list[Trace] = []
    seen: set[Trace] = set()
    for path in sorted(case_dir.glob("global_log_*.txt")):
        match = re.search(r"(\d+)$", path.stem)
        if not match or int(match.group(1)) >= GENERATED_LOG_START:
            continue
        for item in load_global_log(path):
            trace = item.tokens
            if trace in seen:
                continue
            if all(
                accepts_trace(
                    model.tree,
                    project_global_trace(trace, model.sends, model.receives),
                    model.sends,
                    model.receives,
                )
                for model in models
            ):
                seen.add(trace)
                result.append(trace)
    if not result:
        raise RuntimeError(f"no globally accepted normal traces found in {case_dir}")
    return tuple(result[:12])


def _base_message(token: str, messages: frozenset[str]) -> str | None:
    if token in messages:
        return token
    if token.endswith(("_s", "_r")) and token[:-2] in messages:
        return token[:-2]
    return None


def _existing_anomaly_candidates(
    normal_traces: tuple[Trace, ...],
    models: list[ProcessModel],
    rng: random.Random,
):
    messages = frozenset().union(*(model.sends | model.receives for model in models))
    candidates: list[tuple[str, str, tuple[str, ...], Trace]] = []
    for normal in normal_traces:
        for index, token in enumerate(normal):
            base = _base_message(token, messages)
            if base is None:
                continue
            deleted = normal[:index] + normal[index + 1 :]
            candidates.append(("missing_message", f"delete token {token} at position {index + 1}", (base,), deleted))
            if token == base:
                candidates.append(
                    (
                        "sender_only_observation",
                        f"replace {token} with {token}_s at position {index + 1}",
                        (base,),
                        normal[:index] + (f"{token}_s",) + normal[index + 1 :],
                    )
                )
                candidates.append(
                    (
                        "receiver_only_observation",
                        f"replace {token} with {token}_r at position {index + 1}",
                        (base,),
                        normal[:index] + (f"{token}_r",) + normal[index + 1 :],
                    )
                )
        for index in range(len(normal) - 1):
            first = _base_message(normal[index], messages)
            second = _base_message(normal[index + 1], messages)
            if first is None or second is None or first == second:
                continue
            swapped = list(normal)
            swapped[index], swapped[index + 1] = swapped[index + 1], swapped[index]
            candidates.append(
                (
                    "adjacent_swap",
                    f"swap tokens {normal[index]} and {normal[index + 1]} at positions {index + 1}-{index + 2}",
                    (first, second),
                    tuple(swapped),
                )
            )
    rng.shuffle(candidates)
    return candidates


def _manifest_entry(
    *,
    case: str,
    model_origin: str,
    bpmn_path: Path,
    log_path: Path,
    seed: int,
    participant_count: int,
    message_flow_count: int,
    normal_count: int,
    anomaly_type: str,
    ground_truth: str,
    injected_messages: tuple[str, ...],
    expected_participants: tuple[str, ...],
    evaluation: DatasetEvaluation,
) -> ManifestEntry:
    return ManifestEntry(
        case=case,
        model_origin=model_origin,
        bpmn_file=str(bpmn_path.relative_to(REPOSITORY_ROOT)).replace("\\", "/"),
        log_file=str(log_path.relative_to(REPOSITORY_ROOT)).replace("\\", "/"),
        seed=seed,
        participant_count=participant_count,
        message_flow_count=message_flow_count,
        normal_trace_count=normal_count,
        abnormal_trace_count=1,
        anomaly_type=anomaly_type,
        injection_ground_truth=ground_truth,
        injected_messages=injected_messages,
        expected_participants=expected_participants,
        detected_participants=evaluation.affected_participants,
        detected_minas=evaluation.minimal_abnormal_structures,
        strict_rule_ids=evaluation.strict_rule_ids,
        detected=evaluation.detected,
        strict_repair_success=evaluation.strict_repair_success,
        bpmn_sha256=_sha256(bpmn_path),
        log_sha256=_sha256(log_path),
    )


def _generate_synthetic_cases(
    experiments_dir: Path,
    seed: int,
    logs_per_case: int,
    runtime_root: Path,
) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    for case_index, (case_name, participant_count, message_count) in enumerate(SYNTHETIC_SPECS, start=1):
        case_seed = seed + case_index * 10_000
        rng = random.Random(case_seed)
        case_dir = experiments_dir / case_name
        bpmn_path = case_dir / "collaboration.bpmn"
        blocks = _build_synthetic_bpmn(
            case_name,
            case_index,
            participant_count,
            message_count,
            bpmn_path,
        )
        _, models = load_process_models(bpmn_path, runtime_root / case_name)
        normals = _synthetic_normals(blocks, rng)
        candidates = _synthetic_anomaly_candidates(blocks, normals, rng)
        accepted = 0
        seen_abnormal: set[Trace] = set()
        type_counts: dict[str, int] = {}
        anomaly_types = ("missing_message", "sequence_inversion", "choice_conflict")
        quotas = {
            anomaly_type: logs_per_case // len(anomaly_types)
            + (1 if index < logs_per_case % len(anomaly_types) else 0)
            for index, anomaly_type in enumerate(anomaly_types)
        }
        for anomaly_type, ground_truth, messages, expected, abnormal in candidates:
            if type_counts.get(anomaly_type, 0) >= quotas[anomaly_type]:
                continue
            if abnormal in seen_abnormal:
                continue
            evaluation = _evaluate_dataset(models, normals, abnormal)
            if not evaluation.strict_repair_success:
                continue
            log_path = case_dir / f"global_log_{accepted}.txt"
            _write_log(
                log_path,
                seed=case_seed,
                model_origin="synthetic_scaled_collaboration",
                anomaly_type=anomaly_type,
                ground_truth=ground_truth,
                normal_traces=normals,
                abnormal_trace=abnormal,
            )
            entries.append(
                _manifest_entry(
                    case=case_name,
                    model_origin="synthetic_scaled_collaboration",
                    bpmn_path=bpmn_path,
                    log_path=log_path,
                    seed=case_seed,
                    participant_count=participant_count,
                    message_flow_count=message_count,
                    normal_count=len(normals),
                    anomaly_type=anomaly_type,
                    ground_truth=ground_truth,
                    injected_messages=messages,
                    expected_participants=expected,
                    evaluation=evaluation,
                )
            )
            seen_abnormal.add(abnormal)
            type_counts[anomaly_type] = type_counts.get(anomaly_type, 0) + 1
            accepted += 1
            if accepted >= logs_per_case:
                break
        if accepted < logs_per_case:
            raise RuntimeError(
                f"only generated {accepted}/{logs_per_case} strict logs for {case_name}; "
                f"types={type_counts}"
            )
        print(
            f"[synthetic] {case_name}: participants={participant_count}, messages={message_count}, "
            f"logs={accepted}, types={type_counts}"
        )
    return entries


def _augment_existing_cases(
    experiments_dir: Path,
    seed: int,
    logs_per_case: int,
    runtime_root: Path,
) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    for case_index, case_name in enumerate(BASE_CASES, start=1):
        case_seed = seed + 500_000 + case_index * 10_000
        rng = random.Random(case_seed)
        case_dir = experiments_dir / case_name
        bpmn_path = case_dir / "collaboration.bpmn"
        _, models = load_process_models(bpmn_path, runtime_root / f"existing_{case_name}")
        normals = _all_original_normal_traces(case_dir, models)
        candidates = _existing_anomaly_candidates(normals, models, rng)
        accepted = 0
        seen_abnormal: set[Trace] = set()
        type_counts: dict[str, int] = {}
        for anomaly_type, ground_truth, messages, abnormal in candidates:
            if abnormal in seen_abnormal or abnormal in normals:
                continue
            evaluation = _evaluate_dataset(models, normals, abnormal)
            if not evaluation.strict_repair_success:
                continue
            log_path = case_dir / f"global_log_{GENERATED_LOG_START + accepted}.txt"
            _write_log(
                log_path,
                seed=case_seed,
                model_origin="existing_bpmn_mutation",
                anomaly_type=anomaly_type,
                ground_truth=ground_truth,
                normal_traces=normals,
                abnormal_trace=abnormal,
            )
            message_count = len(frozenset().union(*(model.sends | model.receives for model in models)))
            entries.append(
                _manifest_entry(
                    case=case_name,
                    model_origin="existing_bpmn_mutation",
                    bpmn_path=bpmn_path,
                    log_path=log_path,
                    seed=case_seed,
                    participant_count=len(models),
                    message_flow_count=message_count,
                    normal_count=len(normals),
                    anomaly_type=anomaly_type,
                    ground_truth=ground_truth,
                    injected_messages=messages,
                    expected_participants=(),
                    evaluation=evaluation,
                )
            )
            seen_abnormal.add(abnormal)
            type_counts[anomaly_type] = type_counts.get(anomaly_type, 0) + 1
            accepted += 1
            if accepted >= logs_per_case:
                break
        if accepted < logs_per_case:
            raise RuntimeError(
                f"only generated {accepted}/{logs_per_case} strict logs for existing case {case_name}; "
                f"types={type_counts}"
            )
        print(f"[existing] {case_name}: logs={accepted}, types={type_counts}")
    return entries


def _write_manifest(experiments_dir: Path, entries: list[ManifestEntry], seed: int) -> tuple[Path, Path]:
    json_path = experiments_dir / "extended_benchmark_manifest.json"
    csv_path = experiments_dir / "extended_benchmark_manifest.csv"
    payload = {
        "schema_version": 1,
        "generator": "scripts/generate_extended_benchmark.py",
        "seed": seed,
        "entry_count": len(entries),
        "all_detected": all(entry.detected for entry in entries),
        "all_strict_repair_success": all(entry.strict_repair_success for entry in entries),
        "entries": [asdict(entry) for entry in entries],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fieldnames = (
        "case",
        "model_origin",
        "bpmn_file",
        "log_file",
        "seed",
        "participant_count",
        "message_flow_count",
        "normal_trace_count",
        "abnormal_trace_count",
        "anomaly_type",
        "injection_ground_truth",
        "injected_messages",
        "expected_participants",
        "detected_participants",
        "detected_minas",
        "strict_rule_ids",
        "detected",
        "strict_repair_success",
        "bpmn_sha256",
        "log_sha256",
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for entry in entries:
            row = asdict(entry)
            for key in (
                "injected_messages",
                "expected_participants",
                "detected_participants",
                "detected_minas",
                "strict_rule_ids",
            ):
                row[key] = json.dumps(row[key], ensure_ascii=False, sort_keys=True)
            writer.writerow(row)
    return json_path, csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成可复现、带异常注入真值且经当前第7章算法严格验证的扩展实验基准。"
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="固定随机种子")
    parser.add_argument(
        "--synthetic-logs-per-case",
        type=int,
        default=15,
        help="每个规模递增合成BPMN生成的日志数",
    )
    parser.add_argument(
        "--existing-logs-per-case",
        type=int,
        default=8,
        help="每个既有BPMN追加的日志数（从global_log_100开始）",
    )
    parser.add_argument(
        "--experiments-dir",
        type=Path,
        default=REPOSITORY_ROOT / "experiments",
        help="实验目录",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.synthetic_logs_per_case < 1 or args.existing_logs_per_case < 1:
        raise SystemExit("每个案例的日志数必须是正整数")
    experiments_dir = args.experiments_dir.resolve()
    experiments_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pais-benchmark-") as temp:
        runtime_root = Path(temp)
        entries = _generate_synthetic_cases(
            experiments_dir,
            args.seed,
            args.synthetic_logs_per_case,
            runtime_root,
        )
        entries.extend(
            _augment_existing_cases(
                experiments_dir,
                args.seed,
                args.existing_logs_per_case,
                runtime_root,
            )
        )
    json_path, csv_path = _write_manifest(experiments_dir, entries, args.seed)
    print(f"generated entries: {len(entries)}")
    print(f"manifest JSON: {json_path}")
    print(f"manifest CSV:  {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
