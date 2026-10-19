"""Load and validate the GitOps exception register.

The register is declared input. The renderer copies it unchanged into the
GitOps output beside the artifact manifest.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


REGISTER_INPUT = Path("data/gitops/exceptions.yaml")
REGISTER_OUTPUT = "gitops-exceptions.yaml"
SCHEMA_PATH = Path("schemas/gitops-exception-register-v1.schema.json")


def register_text(root: Path) -> str:
    path = root / REGISTER_INPUT
    if not path.is_file():
        raise ValueError(f"missing exception register: {REGISTER_INPUT.as_posix()}")
    return path.read_text()


def rendered_register(root: Path) -> dict[str, str]:
    return {REGISTER_OUTPUT: register_text(root)}


def _schema(root: Path) -> dict:
    return json.loads((root / SCHEMA_PATH).read_text())


def _glob_regex(pattern: str) -> str:
    regex: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            regex.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            regex.append(".*")
            index += 2
        elif pattern[index] == "*":
            regex.append("[^/]*")
            index += 1
        else:
            regex.append(re.escape(pattern[index]))
            index += 1
    return "".join(regex)


def path_matches(pattern: str, relative: str) -> bool:
    if ".." in Path(pattern).parts or ".." in Path(relative).parts:
        return False
    return re.fullmatch(_glob_regex(pattern), relative) is not None


def _decision_errors(root: Path, decision: object) -> list[str]:
    if not isinstance(decision, dict):
        return ["decision must name a bead or a repository path"]
    if "path" not in decision:
        return []
    raw = decision.get("path")
    if not isinstance(raw, str):
        return ["decision path must be a string"]
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        return [f"decision path escapes the repository: {raw}"]
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
        return [f"decision path does not exist: {raw}"]
    return []


def _expiry_error(raw: object, today: date) -> str | None:
    if not isinstance(raw, str):
        return "expiry must be a date"
    try:
        expiry = date.fromisoformat(raw)
    except ValueError:
        return f"expiry is not a date: {raw}"
    if expiry < today:
        return f"waiver expired on {raw}"
    return None


def validate_document(root: Path, document: object, rendered_paths: set[str], today: date | None = None) -> list[str]:
    errors: list[str] = []
    try:
        Draft202012Validator(_schema(root)).validate(document)
    except ValidationError as exc:
        errors.append(exc.message)
        return errors
    if not isinstance(document, dict):
        return ["exception register must be an object"]

    current = today or date.today()
    exceptions = document["spec"]["exceptions"]
    seen: set[str] = set()
    for entry in exceptions:
        pattern = entry["path"]
        if pattern in seen:
            errors.append(f"duplicate exception path: {pattern}")
        seen.add(pattern)
        errors.extend(_decision_errors(root, entry["decision"]))
        if entry.get("category") == "waiver":
            owner = entry.get("owner")
            decision = entry.get("decision")
            linked = isinstance(decision, dict) and (
                isinstance(decision.get("bead"), str) or isinstance(decision.get("path"), str)
            )
            if not isinstance(owner, str) or not owner.strip():
                errors.append(f"waiver requires an owner: {pattern}")
            if not linked:
                errors.append(f"waiver requires a decision link: {pattern}")
            if "expiry" not in entry:
                errors.append(f"waiver requires an expiry: {pattern}")
        if "expiry" in entry:
            expiry_error = _expiry_error(entry["expiry"], current)
            if expiry_error:
                errors.append(expiry_error)
        overlaps = sorted(path for path in rendered_paths if path_matches(pattern, path))
        if overlaps:
            shown = ", ".join(overlaps[:3])
            errors.append(f"exception overlaps rendered path {pattern}: {shown}")
    return errors


def validate_register(root: Path, rendered_paths: set[str], today: date | None = None) -> list[str]:
    document = yaml.safe_load(register_text(root))
    return validate_document(root, document, rendered_paths, today)
