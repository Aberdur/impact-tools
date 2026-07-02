"""Submit prepared EGA metadata drafts to the Submitter Portal API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any



@dataclass(frozen=True)
class SubmitSubmissionConfig:
    """Configuration for submitter-portal draft submission."""

    draft_file: Path
    output_dir: Path
    api_base: str | None = None
    token: str | None = None
    token_file: Path | None = None
    resume_state_file: Path | None = None
    submission_id: str | None = None
    execute: bool = False
    finalise: bool = False
    timeout_seconds: float = 60.0
    verify_tls: bool = True


@dataclass(frozen=True)
class SubmitSubmissionResult:
    """Files written by submit-submission."""

    output_dir: Path
    payload_dir: Path
    response_dir: Path
    state_file: Path
    plan_file: Path


@dataclass(frozen=True)
class SubmissionStep:
    """One ordered API submission step."""

    name: str
    method: str
    path: str
    payload: dict[str, Any]
    enabled: bool = True


def submit_submission(config: SubmitSubmissionConfig) -> SubmitSubmissionResult:
    """Create payloads from a prepared draft and optionally submit them."""
    draft_path = config.draft_file.expanduser().resolve()
    if not draft_path.is_file():
        raise FileNotFoundError(f"Draft file does not exist: {draft_path}")

    draft = _read_mapping_file(draft_path)
    output_dir = config.output_dir.expanduser().resolve()
    payload_dir = output_dir / "payloads"
    response_dir = output_dir / "responses"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)

    token = _read_token(config.token, config.token_file)
    if config.execute and not token:
        raise ValueError("--execute requires --token or --token-file")
    if config.execute and not config.api_base:
        raise ValueError("--execute requires --api-base")

    state_file = output_dir / "submission_state.json"
    state: dict[str, Any] = {
        "mode": "execute" if config.execute else "dry-run",
        "api_base": config.api_base,
        "draft_file": str(draft_path),
        "ids": {},
        "files": {},
        "steps": [],
    }
    _apply_resume_state(state, config.resume_state_file)
    if config.submission_id:
        state.setdefault("ids", {})["submission"] = str(config.submission_id)

    steps = _build_steps(draft, state, include_finalise=config.finalise)
    _write_payloads(payload_dir, steps, state)
    _write_plan(output_dir / "submission_plan.json", steps, state)

    try:
        if config.execute:
            _validate_execute_plan(steps, state)
            _validate_execute_payloads(steps, state)
            _execute_steps(config, token, response_dir, steps, state)
        else:
            _apply_dry_run_ids(steps, state)
    finally:
        state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    return SubmitSubmissionResult(
        output_dir=output_dir,
        payload_dir=payload_dir,
        response_dir=response_dir,
        state_file=state_file,
        plan_file=output_dir / "submission_plan.json",
    )


def _build_steps(draft: dict[str, Any], state: dict[str, Any], include_finalise: bool) -> list[SubmissionStep]:
    submission = _clean_payload(draft.get("submission") or {})
    study = _clean_payload(draft.get("study") or {})
    sample = _clean_payload(draft.get("sample") or {})
    experiment = _clean_payload(draft.get("experiment") or {})
    run = _clean_payload(draft.get("run") or {})
    dataset = _clean_payload(draft.get("dataset") or {})
    finalise = _clean_payload(draft.get("finalise") or {})

    submission_id = _state_id(state, "submission", "{submission_provisional_id}")
    study_id = _state_id(state, "study", "{study_provisional_id}")
    sample_id = _state_id(state, "sample", "{sample_provisional_id}")
    experiment_id = _state_id(state, "experiment", "{experiment_provisional_id}")
    run_id = _state_id(state, "run", "{run_provisional_id}")

    existing_ids = state.get("ids", {})

    expected_files = _expected_run_files(run)
    state["expected_files"] = expected_files
    run["files"] = [_file_placeholder(item["lookup_name"]) for item in expected_files]

    if "study_accession_id" not in experiment and "study_provisional_id" not in experiment:
        experiment["study_provisional_id"] = study_id
    if "experiment_accession_id" not in run and "experiment_provisional_id" not in run:
        run["experiment_provisional_id"] = experiment_id
    if "sample_accession_id" not in run and "sample_provisional_id" not in run:
        run["sample_provisional_id"] = sample_id
    if "run_accession_ids" not in dataset and "run_provisional_ids" not in dataset:
        dataset["run_provisional_ids"] = [run_id]

    return [
        SubmissionStep("01_submission", "POST", "/submissions", submission, enabled=bool(submission) and "submission" not in existing_ids),
        SubmissionStep("02_study", "POST", f"/submissions/{submission_id}/studies", study, enabled=bool(study) and "study" not in existing_ids),
        SubmissionStep("03_sample", "POST", f"/submissions/{submission_id}/samples", sample, enabled=bool(sample) and "sample" not in existing_ids),
        SubmissionStep("04_experiment", "POST", f"/submissions/{submission_id}/experiments", experiment, enabled=bool(experiment) and "experiment" not in existing_ids),
        SubmissionStep(
            "05_resolve_files",
            "GET",
            "/files",
            {"expected_files": expected_files, "status": "inbox"},
            enabled=bool(expected_files) and not _expected_files_already_resolved(expected_files, state),
        ),
        SubmissionStep("06_run", "POST", f"/submissions/{submission_id}/runs", run, enabled=bool(run) and "run" not in existing_ids),
        SubmissionStep("07_dataset", "POST", f"/submissions/{submission_id}/datasets", dataset, enabled=bool(dataset) and "dataset" not in existing_ids),
        SubmissionStep(
            "08_finalise",
            "POST",
            f"/submissions/{submission_id}/finalise",
            finalise,
            enabled=include_finalise and bool(finalise),
        ),
    ]


def _expected_run_files(run: dict[str, Any]) -> list[dict[str, Any]]:
    files = run.get("files")
    if not isinstance(files, list):
        return []
    expected = []
    for item in files:
        if isinstance(item, int):
            continue
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("relative_path") or item.get("display_name")
        if not name:
            continue
        encrypted_name = name if str(name).endswith(".c4gh") else f"{name}.c4gh"
        expected.append(
            {
                "source_name": name,
                "lookup_name": encrypted_name,
                "unencrypted_md5": item.get("md5"),
                "extension": item.get("extension"),
            }
        )
    return expected


def _file_placeholder(name: str) -> str:
    return "{file_provisional_id:" + name + "}"


def _expected_files_already_resolved(expected_files: list[dict[str, Any]], state: dict[str, Any]) -> bool:
    files = state.get("files", {})
    return bool(expected_files) and all(item.get("lookup_name") in files for item in expected_files)


def _validate_execute_plan(steps: list[SubmissionStep], state: dict[str, Any]) -> None:
    entity_by_step = {
        "01_submission": "submission",
        "02_study": "study",
        "03_sample": "sample",
        "04_experiment": "experiment",
        "06_run": "run",
        "07_dataset": "dataset",
    }
    required_steps = ["01_submission", "02_study", "03_sample", "04_experiment", "05_resolve_files", "06_run", "07_dataset"]
    existing_ids = state.get("ids", {})
    disabled = []
    for step in steps:
        if step.name not in required_steps or step.enabled:
            continue
        if step.name == "05_resolve_files" and _expected_files_already_resolved(state.get("expected_files") or [], state):
            continue
        entity = entity_by_step.get(step.name)
        if entity and entity in existing_ids:
            continue
        disabled.append(step.name)
    if disabled:
        raise ValueError(
            "Cannot execute submission because required payloads are empty: " + ", ".join(disabled)
        )


def _validate_execute_payloads(steps: list[SubmissionStep], state: dict[str, Any]) -> None:
    errors: list[str] = []
    for step in steps:
        if not step.enabled or step.name == "05_resolve_files":
            continue
        payload = _resolve_placeholders(step.payload, state)
        for path, value in _walk_payload(payload):
            if isinstance(value, str) and value.startswith("EXAMPLE:"):
                errors.append(f"{step.name}.{path} contains placeholder example value: {value!r}")
        if step.name == "04_experiment":
            errors.extend(_validate_experiment_payload(payload))
        if step.name == "07_dataset":
            errors.extend(_validate_dataset_payload(payload))
    if errors:
        raise ValueError("Cannot execute submission because payload validation failed:\n- " + "\n- ".join(errors))


def _validate_experiment_payload(payload: dict[str, Any]) -> list[str]:
    errors = []
    required = [
        "design_description",
        "instrument_model_id",
        "library_layout",
        "library_strategy",
        "library_source",
        "library_selection",
        "study_provisional_id",
    ]
    for key in required:
        if key not in payload:
            errors.append(f"04_experiment.{key} is required")
    if "instrument_model_id" in payload and not isinstance(payload["instrument_model_id"], int):
        errors.append("04_experiment.instrument_model_id must be an integer platform model id")
    if "study_provisional_id" in payload and not isinstance(payload["study_provisional_id"], int):
        errors.append("04_experiment.study_provisional_id must be an integer")
    return errors


def _validate_dataset_payload(payload: dict[str, Any]) -> list[str]:
    errors = []
    for key in ("title", "description", "dataset_types", "policy_accession_id"):
        if key not in payload:
            errors.append(f"07_dataset.{key} is required")
    dataset_types = payload.get("dataset_types")
    if "dataset_types" in payload and not (
        isinstance(dataset_types, list) and all(isinstance(item, str) for item in dataset_types)
    ):
        errors.append("07_dataset.dataset_types must be a list of strings")
    run_ids = payload.get("run_provisional_ids")
    if run_ids is not None and not (isinstance(run_ids, list) and all(isinstance(item, int) for item in run_ids)):
        errors.append("07_dataset.run_provisional_ids must be a list of integers")
    policy_id = payload.get("policy_accession_id")
    if isinstance(policy_id, str) and not policy_id.startswith("EGAP"):
        errors.append("07_dataset.policy_accession_id must be an EGAP accession")
    return errors


def _walk_payload(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        items = []
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_walk_payload(item, child_prefix))
        return items
    if isinstance(value, list):
        items = []
        for index, item in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            items.extend(_walk_payload(item, child_prefix))
        return items
    return [(prefix, value)]


def _resolve_files(client: Any, payload: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    expected_files = payload.get("expected_files") or []
    resolved = []
    missing = []
    for expected in expected_files:
        lookup_name = expected["lookup_name"]
        candidates = _fetch_file_candidates(client, lookup_name)
        match = _select_file_candidate(candidates, expected)
        if match is None:
            missing.append({"expected": expected, "candidates": candidates})
            continue
        file_id = match.get("provisional_id")
        if file_id is None:
            missing.append({"expected": expected, "candidates": candidates, "reason": "matched file has no provisional_id"})
            continue
        state.setdefault("files", {})[lookup_name] = file_id
        resolved.append({"expected": expected, "file": match, "provisional_id": file_id})
    if missing:
        raise RuntimeError("Could not resolve all Inbox files for RunRequest.files: " + json.dumps(missing, ensure_ascii=False)[:2000])
    return {"resolved_files": resolved}


def _fetch_file_candidates(client: Any, lookup_name: str) -> list[dict[str, Any]]:
    prefixes = [lookup_name]
    if lookup_name.endswith(".c4gh"):
        prefixes.append(lookup_name.removesuffix(".c4gh"))
    candidates: list[dict[str, Any]] = []
    seen = set()
    for prefix in prefixes:
        response = client.get("/files", params={"status": "inbox", "prefix": prefix})
        response_payload = _read_response_json(response)
        response.raise_for_status()
        _extend_unique_candidates(candidates, seen, _extract_records(response_payload))
    if not candidates:
        response = client.get("/files", params={"status": "inbox"})
        response_payload = _read_response_json(response)
        response.raise_for_status()
        inbox_records = _extract_records(response_payload)
        _extend_unique_candidates(
            candidates,
            seen,
            [record for record in inbox_records if _file_candidate_matches_lookup(record, lookup_name)],
        )
    return candidates


def _extract_records(response_payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = response_payload.get("response") if isinstance(response_payload.get("response"), list) else response_payload
    if isinstance(records, dict):
        records = records.get("items") or records.get("results") or records.get("data") or []
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _extend_unique_candidates(candidates: list[dict[str, Any]], seen: set[str], records: list[dict[str, Any]]) -> None:
    for record in records:
        key = json.dumps(record, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(record)


def _file_candidate_matches_lookup(candidate: dict[str, Any], lookup_name: str) -> bool:
    names = [
        candidate.get("relative_path"),
        candidate.get("display_name"),
        candidate.get("name"),
    ]
    for name in names:
        if not name:
            continue
        name_text = str(name)
        if name_text == lookup_name or name_text.endswith(f"/{lookup_name}") or Path(name_text).name == lookup_name:
            return True
    return False


def _select_file_candidate(candidates: list[dict[str, Any]], expected: dict[str, Any]) -> dict[str, Any] | None:
    lookup_name = expected["lookup_name"]
    source_name = expected.get("source_name")
    unencrypted_md5 = expected.get("unencrypted_md5")
    exact_name_matches = []
    for candidate in candidates:
        names = [
            candidate.get("relative_path"),
            candidate.get("display_name"),
            candidate.get("name"),
        ]
        basenames = [Path(str(name)).name for name in names if name]
        if lookup_name in names or lookup_name in basenames or source_name in names or source_name in basenames:
            exact_name_matches.append(candidate)
    if unencrypted_md5:
        checksum_matches = [
            candidate for candidate in exact_name_matches or candidates
            if candidate.get("unencrypted_checksum") == unencrypted_md5
        ]
        if len(checksum_matches) == 1:
            return checksum_matches[0]
    if len(exact_name_matches) == 1:
        return exact_name_matches[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _execute_steps(
    config: SubmitSubmissionConfig,
    token: str | None,
    response_dir: Path,
    steps: list[SubmissionStep],
    state: dict[str, Any],
) -> None:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "impact-tools",
    }
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx is required for --execute. Install project dependencies first.") from exc

    with httpx.Client(
        base_url=(config.api_base or "").rstrip("/"),
        timeout=config.timeout_seconds,
        verify=config.verify_tls,
        headers=headers,
    ) as client:
        for step in steps:
            if not step.enabled:
                _record_step(state, step, skipped=True)
                continue
            path = _resolve_path(step.path, state)
            payload = _resolve_placeholders(step.payload, state)
            if step.name == "05_resolve_files":
                response_payload = _resolve_files(client, payload, state)
                (response_dir / f"{step.name}.json").write_text(
                    json.dumps(response_payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                _record_step(state, step, path=path, status_code=200)
                continue
            response = client.request(step.method, path, json=payload)
            response_payload = _read_response_json(response)
            (response_dir / f"{step.name}.json").write_text(
                json.dumps(response_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            response.raise_for_status()
            _update_state_ids(state, step.name, response_payload)
            _record_step(state, step, path=path, status_code=response.status_code)


def _apply_dry_run_ids(steps: list[SubmissionStep], state: dict[str, Any]) -> None:
    dry_ids = {
        "submission": "DRY_RUN_SUBMISSION_PROVISIONAL_ID",
        "study": "DRY_RUN_STUDY_PROVISIONAL_ID",
        "sample": "DRY_RUN_SAMPLE_PROVISIONAL_ID",
        "experiment": "DRY_RUN_EXPERIMENT_PROVISIONAL_ID",
        "run": "DRY_RUN_RUN_PROVISIONAL_ID",
        "dataset": "DRY_RUN_DATASET_PROVISIONAL_ID",
    }
    state.setdefault("ids", {}).update(dry_ids)
    expected_files = state.get("expected_files") or []
    state.setdefault("files", {}).update(
        {item["lookup_name"]: f"DRY_RUN_FILE_PROVISIONAL_ID_{index}" for index, item in enumerate(expected_files, start=1)}
    )
    for step in steps:
        _record_step(state, step, path=_resolve_path(step.path, state), skipped=not step.enabled)


def _update_state_ids(state: dict[str, Any], step_name: str, payload: dict[str, Any]) -> None:
    entity_by_step = {
        "01_submission": "submission",
        "02_study": "study",
        "03_sample": "sample",
        "04_experiment": "experiment",
        "06_run": "run",
        "07_dataset": "dataset",
    }
    entity = entity_by_step.get(step_name)
    if not entity:
        return
    identifier = _extract_identifier(payload)
    if identifier:
        state.setdefault("ids", {})[entity] = identifier


def _extract_identifier(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("provisional_id", "accession_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, int):
                return str(value)
        for value in payload.values():
            found = _extract_identifier(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _extract_identifier(item)
            if found:
                return found
    return None


def _record_step(
    state: dict[str, Any],
    step: SubmissionStep,
    *,
    path: str | None = None,
    status_code: int | None = None,
    skipped: bool = False,
) -> None:
    state.setdefault("steps", []).append(
        {
            "name": step.name,
            "method": step.method,
            "path": path or step.path,
            "enabled": step.enabled,
            "skipped": skipped,
            "status_code": status_code,
        }
    )


def _write_payloads(payload_dir: Path, steps: list[SubmissionStep], state: dict[str, Any]) -> None:
    for step in steps:
        payload = _resolve_placeholders(step.payload, state)
        (payload_dir / f"{step.name}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _write_plan(path: Path, steps: list[SubmissionStep], state: dict[str, Any]) -> None:
    plan = {
        "mode": state["mode"],
        "api_base": state.get("api_base"),
        "steps": [
            {
                "name": step.name,
                "method": step.method,
                "path": step.path,
                "payload_file": f"payloads/{step.name}.json",
                "enabled": step.enabled,
            }
            for step in steps
        ],
    }
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")


def _clean_payload(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            clean_item = _clean_payload(item)
            if _has_payload_value(clean_item):
                cleaned[key] = clean_item
        return cleaned
    if isinstance(value, list):
        cleaned_list = [_clean_payload(item) for item in value]
        return [item for item in cleaned_list if _has_payload_value(item)]
    return value


def _has_payload_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


def _state_id(state: dict[str, Any], key: str, placeholder: str) -> str:
    return state.get("ids", {}).get(key) or placeholder


def _resolve_path(path: str, state: dict[str, Any]) -> str:
    return str(_resolve_placeholders(path, state))


def _resolve_placeholders(value: Any, state: dict[str, Any]) -> Any:
    ids = state.get("ids", {})
    replacements = {
        "{submission_provisional_id}": ids.get("submission", "{submission_provisional_id}"),
        "{study_provisional_id}": ids.get("study", "{study_provisional_id}"),
        "{sample_provisional_id}": ids.get("sample", "{sample_provisional_id}"),
        "{experiment_provisional_id}": ids.get("experiment", "{experiment_provisional_id}"),
        "{run_provisional_id}": ids.get("run", "{run_provisional_id}"),
    }
    if isinstance(value, str):
        if value in replacements:
            replacement = replacements[value]
            if isinstance(replacement, str) and replacement.isdigit():
                return int(replacement)
            return replacement
        for old, new in replacements.items():
            value = value.replace(old, str(new))
        file_match = _file_placeholder_pattern(value)
        if file_match is not None:
            return state.get("files", {}).get(file_match, value)
        return value
    if isinstance(value, dict):
        return {key: _resolve_placeholders(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(item, state) for item in value]
    return value


def _file_placeholder_pattern(value: str) -> str | None:
    prefix = "{file_provisional_id:"
    if value.startswith(prefix) and value.endswith("}"):
        return value[len(prefix):-1]
    return None


def _read_token(token: str | None, token_file: Path | None) -> str | None:
    if token:
        return token.strip()
    if token_file is None:
        return None
    path = token_file.expanduser().resolve()
    payload = path.read_text(encoding="utf-8").strip()
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    if isinstance(parsed, dict):
        for key in ("access_token", "token", "bearer"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                return value
    raise ValueError(f"Token file does not contain access_token/token/bearer: {path}")


def _apply_resume_state(state: dict[str, Any], resume_state_file: Path | None) -> None:
    if resume_state_file is None:
        return
    path = resume_state_file.expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Resume state must contain a mapping: {path}")
    ids = payload.get("ids")
    if isinstance(ids, dict):
        state.setdefault("ids", {}).update({key: str(value) for key, value in ids.items() if value is not None})
    files = payload.get("files")
    if isinstance(files, dict):
        state.setdefault("files", {}).update(files)
    state["resume_state_file"] = str(path)


def _read_response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Submitter Portal API returned a non-JSON response.\n"
            f"URL: {response.request.url}\n"
            f"Response: {response.text[:1000]}"
        ) from exc
    if not isinstance(payload, dict):
        return {"response": payload}
    return payload


def _read_mapping_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - project depends on PyYAML
            raise RuntimeError("PyYAML is required to read YAML draft files.") from exc
        payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Draft submission must contain a mapping: {path}")
    return payload
