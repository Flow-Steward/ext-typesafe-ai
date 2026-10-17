"""Verify the extension with the actual target Flow Steward validators."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from core.application.orchestration import connector_step_runtime as runtime
from core.application.orchestration.control_flow_step_node import control_flow_step_node
from core.application.orchestration.run_definition import clear_run_definition_cache
from core.application.orchestration.step_hub import orchestrator_hub_node
from core.application.registry.workflow_contract import WorkflowInterface
from core.application.registry.workflow_contract_service import WorkflowInterfaceService
from core.application.registry.workflow_secret_defaults import secret_ref_fingerprint
from core.application.tooling.extensions.bundle_extension import bundle_extension
from core.application.tooling.extensions.models import (
    ExtensionBundleRequest,
    ExtensionValidationRequest,
)
from core.application.tooling.extensions.validate_extensions import validate_extensions
from core.application.workflows.workflow_definition_validation import (
    validate_workflow_definition,
)
from core.application.workflows.workflow_portability import WorkflowPortabilityService
from core.infrastructure.extension_runtime.extension_runtime_compile import (
    compile_extension_bundle,
)
from core.infrastructure.security.secret_reference import (
    SecretReference,
    is_secret_reference,
)
from tests._step_run_state import prime
from tests.unit.test_workflow_portability import _PortabilityDB

ROOT = Path(__file__).resolve().parents[1]


def test_bundle_compiles_with_project_connection_ui():
    compiled = compile_extension_bundle(ROOT)
    assert compiled["status"] == "valid", compiled


def test_public_validator_health_and_bundler_accept_the_release(tmp_path):
    validation = validate_extensions(
        ExtensionValidationRequest(
            extension_id="flowsteward.typesafe-ai",
            root=ROOT.parent,
            health=True,
            marketplace=True,
        )
    )
    assert validation.exit_code == 0, validation.output
    result = bundle_extension(
        ExtensionBundleRequest(
            extension_id="flowsteward.typesafe-ai", root=ROOT.parent, out_dir=tmp_path
        )
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "flowsteward.typesafe-ai-1.0.2.zip").is_file()


def bundle():
    return (ROOT / "examples/jev_support_triage-1.0.1.yaml").read_text()


def test_parent_can_supply_the_child_model_at_the_actual_input_boundary():
    parent, child = yaml.safe_load(bundle())["workflows"]
    invoke = next(step for step in parent["steps"] if step["step_id"] == "classify")
    model_source = invoke["override"]["invoke_workflow"]["input_mapping"]["model"]
    assert model_source.startswith("literal:")
    result = WorkflowInterfaceService().validate_delegated_inputs(
        WorkflowInterface.from_settings(child["settings"]),
        {
            "state": {"message": "Production API outage"},
            "questions": {"urgent": {"type": "noul", "instructions": "Urgent?"}},
            "model": model_source.removeprefix("literal:"),
        },
    )
    assert result.ok, result.errors
    assert result.inputs["model"] == "jev-latest"


def test_example_import_creates_both_workflows_and_preserves_the_child_call():
    db = _PortabilityDB(
        installs={
            "flowsteward.typesafe-ai": {
                "install_id": "flowsteward.typesafe-ai",
                "status": "active",
            }
        },
        connections={"jev-example": {"connection_id": "jev-example", "enabled": True}},
    )
    service = WorkflowPortabilityService(db)
    resolutions = [
        {
            "dependency_type": "project_connection",
            "ref": "jev-example",
            "action": "map_existing",
            "target_connection_id": "jev-example",
        }
    ]
    preflight = service.preflight_import(bundle(), resolutions=resolutions)
    assert preflight["can_apply"] is True, json.dumps(preflight, indent=2)
    result = service.apply_import(bundle(), resolutions=resolutions)
    assert result["workflow_id"] == "jev_support_triage"
    assert set(db.workflows) == {"jev_evaluate", "jev_support_triage"}
    for row in db.workflows.values():
        assert validate_workflow_definition(service._workflow_definition(row)) == []
    invoke = next(
        s
        for s in db.workflows["jev_support_triage"]["steps"]
        if s["step_kind"] == "invoke_workflow"
    )
    assert invoke["override"]["invoke_workflow"]["workflow_id"] == "jev_evaluate"
    # Compare the saved mappings, not just structural validity. The importer
    # masks values under credential-shaped keys, including reference strings.
    for source in yaml.safe_load(bundle())["workflows"]:
        saved_steps = {
            row["step_id"]: row for row in db.workflows[source["workflow_id"]]["steps"]
        }
        for step in source["steps"]:
            saved = saved_steps[step["step_id"]]
            if "input_mapping_jsonb" in step:
                assert saved["input_mapping_jsonb"] == step["input_mapping_jsonb"]
            invocation = step.get("override", {}).get("invoke_workflow")
            if invocation:
                assert (
                    saved["override"]["invoke_workflow"]["input_mapping"]
                    == invocation["input_mapping"]
                )


@pytest.mark.parametrize(
    ("category", "confidence", "urgency_confidence", "human", "expected"),
    [
        ("billing", 0.9, 0.9, 0.1, "billing"),
        ("technical", 0.7, 0.8, 0.1, "technical"),
        ("sales", 0.9, 0.9, 0.1, "sales"),
        ("general", 0.9, 0.9, 0.1, "general"),
        ("billing", 0.69, 0.9, 0.1, "manual_review"),
        ("billing", 0.9, 0.5, 0.1, "manual_review"),
        ("billing", 0.9, 0.9, 0.8, "manual_review"),
        ("unexpected", 0.9, 0.9, 0.1, "manual_review"),
    ],
)
def test_real_workflow_graph_routes_uncertain_and_human_cases_to_review(
    category, confidence, urgency_confidence, human, expected
):
    definition = copy.deepcopy(yaml.safe_load(bundle())["workflows"][0])
    definition["execution_graph"]["entry"] = {
        "kind": "step",
        "id": "category_confident",
    }
    state = {
        "workflow_id": "jev_support_triage",
        "agent_messages": [],
        "inputs": {"min_confidence": 0.7, "human_threshold": 0.8},
        "classification": {
            "category": {"choice": category, "confidence": confidence},
            "urgency": {"score": 2.0, "confidence": urgency_confidence},
            "requires_human": {"noul": human},
        },
    }
    clear_run_definition_cache()
    state.update(prime(definition))
    with (
        patch("core.application.orchestration.agents.common.emit_event"),
        patch(
            "core.application.orchestration.agents.common.publish_agent_message",
            return_value=True,
        ),
    ):
        for _ in range(15):
            command = orchestrator_hub_node(state)
            state.update(command.update)
            if command.goto == "orchestrator_validation":
                break
            assert command.goto == "control_flow_step", command
            state.update(control_flow_step_node(state))
        else:
            pytest.fail("classification routing did not terminate")
    assert state["routing_key"] == expected
    assert state["needs_review"] is (expected == "manual_review")
    assert state["workflow_terminal"]["kind"] == "stop_success"
    clear_run_definition_cache()


def test_workflow_key_is_resolved_only_for_dispatch_and_absent_from_result(monkeypatch):
    captured = {}

    def dispatch(**kwargs):
        captured.update(kwargs["input_payload"])
        return {"handled": True, "ok": True, "result": {"answers": {}}}

    monkeypatch.setattr(runtime, "_dispatch_tool_runtime", dispatch)
    monkeypatch.setattr(
        runtime, "_read_secret_ciphertext", lambda account_id, ref_key: "encrypted"
    )
    monkeypatch.setattr(
        runtime, "_decrypt_secret_value", lambda ciphertext: "private-run-credential"
    )
    reference = SecretReference(
        name="api_key", ref_key="workflow:triage:input:api_key"
    ).to_wire()
    child = yaml.safe_load(bundle())["workflows"][1]
    step = copy.deepcopy(
        next(s for s in child["steps"] if s["step_kind"] == "connector")
    )
    # Advanced workflows can still use the connector's optional secret override.
    # The importable example uses only the saved project connection.
    step["input_mapping_jsonb"]["api_key"] = "state.inputs.api_key"
    result = runtime.execute_connector_step(
        step=step,
        state={
            "inputs": {
                "api_key": reference,
                "state": {"message": "Refund"},
                "questions": {"urgent": {"type": "noul", "instructions": "Urgent?"}},
                "model": "jev-latest",
            },
            "runtime_context": {"account_id": "acc-1"},
            "_workflow_secret_ref_fingerprints": [
                secret_ref_fingerprint("workflow:triage:input:api_key")
            ],
        },
    )
    assert captured["api_key"] == "private-run-credential"
    assert is_secret_reference(result["input_payload"]["api_key"])
    assert "private-run-credential" not in json.dumps(result)
