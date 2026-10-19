#!/usr/bin/env python3
"""Validate the SAIF-owned CLIFE package contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "schemas" / "clife-package-contract-v1.schema.json"


def identity(resource: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(resource.get("apiVersion", "")),
        str(resource.get("kind", "")),
        str(resource.get("namespace", "")),
        str(resource.get("name", "")),
    )


def walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys(child)


def require_unique(label: str, values: list[Any]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicate entries")


def require_closed_object_schemas(value: Any, path: str) -> None:
    """Reject value schemas that leave any object key-space open."""
    if isinstance(value, dict):
        if value.get("type") == "object" and value.get("additionalProperties") is not False:
            raise ValueError(f"{path} object schema must set additionalProperties=false")
        for key, child in value.items():
            require_closed_object_schemas(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            require_closed_object_schemas(child, f"{path}[{index}]")


def validate_semantics(contract: dict[str, Any]) -> None:
    spec = contract["spec"]
    release_version = spec["release"]["cilium"]
    if spec["release"]["clife"] != release_version or spec["chart"]["appVersion"] != release_version:
        raise ValueError("Cilium, CLIFE and package appVersion must select the same release")
    inventory = [identity(item) for item in spec["resources"]["exactSourceInventory"]]
    required = [identity(item) for item in spec["resources"]["requiredOutputInventory"]]
    require_unique("resources.exactSourceInventory", inventory)
    require_unique("resources.requiredOutputInventory", required)

    inventory_set = set(inventory)
    if not set(required).issubset(inventory_set):
        raise ValueError("requiredOutputInventory must be a subset of exactSourceInventory")
    for mode, mode_spec in spec["modes"].items():
        excluded = [identity(item) for item in mode_spec["exclude"]]
        require_unique(f"modes.{mode}.exclude", excluded)
        missing = set(excluded) - inventory_set
        if missing:
            raise ValueError(f"modes.{mode}.exclude references resources absent from source inventory: {sorted(missing)}")
        removed_required = set(excluded) & set(required)
        if removed_required:
            raise ValueError(f"modes.{mode}.exclude removes required output resources: {sorted(removed_required)}")

    transformations = spec["transformations"]
    for name in ("controller", "managerRole", "ciliumConfig"):
        target = identity(transformations[name]["resource"])
        if target not in inventory_set:
            raise ValueError(f"transformations.{name}.resource is absent from exactSourceInventory")

    generated = transformations.get("generatedResources", [])
    generated_identities = [identity(item) for item in generated]
    require_unique("transformations.generatedResources", generated_identities)
    hosted_network = ("operator.openshift.io/v1", "Network", "", "cluster")
    if hosted_network not in generated_identities:
        raise ValueError("hosted package must generate operator.openshift.io/v1 Network/cluster")

    template = spec["chart"]["versionTemplate"]
    for token in ("{sourceSha12}", "{contractSha12}"):
        if token not in template:
            raise ValueError(f"chart.versionTemplate must include {token}")

    forbidden = set(spec["values"]["forbiddenKeys"])
    forbidden_present = forbidden.intersection(walk_keys(contract))
    if forbidden_present:
        # The keys are permitted only in the declaration of the deny-list itself.
        declared = set(spec["values"]["forbiddenKeys"])
        occurrences = [key for key in walk_keys({"spec": {k: v for k, v in spec.items() if k != "values"}}) if key in declared]
        if occurrences:
            raise ValueError(f"forbidden prerelease value keys occur in package intent: {sorted(set(occurrences))}")

    images = spec["runtimeImages"]
    require_unique("runtimeImages.name", [str(item["name"]) for item in images])
    require_unique("runtimeImages.source", [str(item["source"]) for item in images])
    for item in images:
        if "@" in item["source"]:
            raise ValueError(f"runtime image source must keep digest in the digest field: {item['name']}")

    cilium_schema = spec["values"]["ciliumConfig"]["schema"]
    network_schema = spec["values"]["networkOperator"]["schema"]
    require_closed_object_schemas(cilium_schema, "values.ciliumConfig.schema")
    require_closed_object_schemas(network_schema, "values.networkOperator.schema")

    network_required = set(network_schema.get("required", []))
    expected_network = {
        "managementState",
        "clusterNetwork",
        "serviceNetwork",
        "defaultNetwork",
        "deployKubeProxy",
    }
    if network_required != expected_network:
        raise ValueError("hosted Network values must require the complete OpenShift 4.19 ownership set")
    cilium_required = set(cilium_schema.get("required", []))
    if "kubeProxyReplacement" not in cilium_required or "tunnelPort" not in cilium_required:
        raise ValueError("hosted CiliumConfig values must require KPR and the nested VXLAN port")

    scenarios = spec["embeddedChartVerification"]["scenarios"]
    require_unique("embeddedChartVerification.scenarios.name", [item["name"] for item in scenarios])
    expected_scenarios = {
        "management": "management",
        "tenant-a": "hosted",
        "shared-ab": "hosted",
    }
    actual_scenarios = {item["name"]: item["mode"] for item in scenarios}
    if actual_scenarios != expected_scenarios:
        raise ValueError("embedded chart scenarios must be exact management, tenant-a and shared-ab targets")

    image_names = {str(item["name"]) for item in images}
    selected: set[str] = set()
    for scenario in scenarios:
        expected_images = list(scenario["expectedRuntimeImages"])
        require_unique(f"embeddedChartVerification.scenarios.{scenario['name']}.expectedRuntimeImages", expected_images)
        unknown = set(expected_images) - image_names
        if unknown:
            raise ValueError(f"embedded chart scenario {scenario['name']} references unknown images: {sorted(unknown)}")
        if "clife" in expected_images:
            raise ValueError("embedded Cilium chart scenarios must not select the CLIFE controller image")
        selected.update(expected_images)
        if scenario["mode"] == "hosted":
            jsonschema.Draft202012Validator(cilium_schema).validate(scenario["values"])

    expected_selected = image_names - {"clife"}
    if selected != expected_selected:
        raise ValueError(
            "runtimeImages must be the exact union of embedded chart scenario images plus clife: "
            f"missing={sorted(expected_selected - selected)}, unexpected={sorted(selected - expected_selected)}"
        )


def validate_contract(contract_path: Path, schema_path: Path = DEFAULT_SCHEMA) -> dict[str, Any]:
    contract = yaml.safe_load(contract_path.read_text())
    schema = json.loads(schema_path.read_text())
    jsonschema.Draft202012Validator(schema).validate(contract)
    validate_semantics(contract)
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()
    validate_contract(args.contract, args.schema)
    print(f"validated {args.contract}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
