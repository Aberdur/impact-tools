"""Normalize and compare EGA encryption, upload and workflow manifests."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from impact_tools.ega.html_report import write_comparison_report


BYTES_IN_GIB = 1024**3
FIELDS = [
    "run_type",
    "run_id",
    "profile",
    "hostname",
    "input_gib",
    "stage_wall_seconds",
    "encryption_stage_wall_seconds",
    "encryption_mib_s",
    "upload_stage_wall_seconds",
    "upload_mib_s",
    "wall_seconds",
    "max_rss_mib",
    "encrypted_files",
    "uploaded_files",
    "cpu_model",
    "cpu_count",
    "memory_gib",
    "slurm_job_id",
    "slurm_partition",
    "slurm_cpus_per_task",
    "slurm_mem_per_node",
]


@dataclass(frozen=True)
class RunComparisonResult:
    output_dir: Path
    metrics_file: Path
    report_file: Path
    runs: int


def compare_runs(
    manifests: tuple[Path, ...],
    output_dir: Path,
    generate_charts: bool = True,
) -> RunComparisonResult:
    """Create a compact table and HTML dashboard from supported manifests."""
    resolved_output = output_dir.expanduser().resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    rows = [_manifest_row(path.expanduser().resolve()) for path in manifests]
    metrics_file = resolved_output / "ega_run_comparison.tsv"
    report_file = resolved_output / "ega_run_comparison.html"

    with metrics_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    write_comparison_report(report_file, rows, include_charts=generate_charts)
    return RunComparisonResult(
        output_dir=resolved_output,
        metrics_file=metrics_file,
        report_file=report_file,
        runs=len(rows),
    )


def _manifest_row(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    run_type = _manifest_type(payload)
    if run_type == "workflow":
        return _workflow_row(payload)
    if run_type == "encryption":
        return _encryption_row(payload)
    return _upload_row(payload)


def _manifest_type(payload: dict) -> str:
    if "encryption" in payload and "upload" in payload:
        return "workflow"
    summary = payload.get("summary", {})
    if "total_encryption_seconds" in summary:
        return "encryption"
    if "total_upload_seconds" in summary:
        return "upload"
    raise ValueError(
        "Unsupported manifest: expected encryption, Inbox upload or workflow data"
    )


def _workflow_row(payload: dict) -> dict:
    execution = payload["execution"]
    encryption = payload["encryption"]
    upload = payload["upload"]
    input_bytes = encryption["processed_input_bytes"]
    encryption_seconds = encryption["total_encryption_seconds"]
    upload_seconds = upload["total_upload_seconds"]
    return _common_row(
        run_type="workflow",
        run_id=payload["run_id"],
        execution=execution,
        input_gib=round(input_bytes / BYTES_IN_GIB, 6),
        stage_wall_seconds=payload["wall_seconds"],
        encryption_stage_wall_seconds=encryption["process"]["wall_seconds"],
        encryption_mib_s=_throughput(input_bytes, encryption_seconds),
        upload_stage_wall_seconds=upload["process"]["wall_seconds"],
        upload_mib_s=_throughput(upload["uploaded_input_bytes"], upload_seconds),
        wall_seconds=payload["wall_seconds"],
        max_rss_mib=max(
            encryption["process"]["max_rss_mib"],
            upload["process"]["max_rss_mib"],
        ),
        encrypted_files=encryption["encrypted"],
        uploaded_files=upload["uploaded"],
    )


def _encryption_row(payload: dict) -> dict:
    execution = payload["execution"]
    summary = payload["summary"]
    input_bytes = summary["processed_input_bytes"]
    process = summary["process"]
    return _common_row(
        run_type="encryption",
        run_id=payload["run"]["run_id"],
        execution=execution,
        input_gib=round(summary["total_input_bytes"] / BYTES_IN_GIB, 6),
        stage_wall_seconds=process["wall_seconds"],
        encryption_stage_wall_seconds=process["wall_seconds"],
        encryption_mib_s=_throughput(
            input_bytes,
            summary["total_encryption_seconds"],
        ),
        wall_seconds=process["wall_seconds"],
        max_rss_mib=process["max_rss_mib"],
        encrypted_files=summary["encrypted"],
    )


def _upload_row(payload: dict) -> dict:
    execution = payload["execution"]
    summary = payload["summary"]
    process = summary["process"]
    return _common_row(
        run_type="upload",
        run_id=payload["run"]["run_id"],
        execution=execution,
        input_gib=round(summary["total_input_bytes"] / BYTES_IN_GIB, 6),
        stage_wall_seconds=process["wall_seconds"],
        upload_stage_wall_seconds=process["wall_seconds"],
        upload_mib_s=_throughput(
            summary["uploaded_input_bytes"],
            summary["total_upload_seconds"],
        ),
        wall_seconds=process["wall_seconds"],
        max_rss_mib=process["max_rss_mib"],
        uploaded_files=summary["uploaded"],
    )


def _common_row(
    *,
    run_type: str,
    run_id: str,
    execution: dict,
    input_gib: float,
    stage_wall_seconds: float,
    encryption_stage_wall_seconds: float | None = None,
    encryption_mib_s: float | None = None,
    upload_stage_wall_seconds: float | None = None,
    upload_mib_s: float | None = None,
    wall_seconds: float | None = None,
    max_rss_mib: float | None = None,
    encrypted_files: int | None = None,
    uploaded_files: int | None = None,
) -> dict:
    return {
        "run_type": run_type,
        "run_id": run_id,
        "profile": execution["profile"],
        "hostname": execution["hostname"],
        "input_gib": input_gib,
        "stage_wall_seconds": stage_wall_seconds,
        "encryption_stage_wall_seconds": encryption_stage_wall_seconds,
        "encryption_mib_s": encryption_mib_s,
        "upload_stage_wall_seconds": upload_stage_wall_seconds,
        "upload_mib_s": upload_mib_s,
        "wall_seconds": wall_seconds,
        "max_rss_mib": max_rss_mib,
        "encrypted_files": encrypted_files,
        "uploaded_files": uploaded_files,
        "cpu_model": execution.get("cpu_model"),
        "cpu_count": execution.get("cpu_count"),
        "memory_gib": _gib(execution.get("memory_bytes")),
        "slurm_job_id": execution.get("slurm_job_id"),
        "slurm_partition": execution.get("slurm_partition"),
        "slurm_cpus_per_task": execution.get("slurm_cpus_per_task"),
        "slurm_mem_per_node": execution.get("slurm_mem_per_node"),
    }


def _throughput(size: int, seconds: float) -> float | None:
    return round((size / (1024**2)) / seconds, 6) if seconds > 0 else None


def _gib(size: int | None) -> float | None:
    return round(size / BYTES_IN_GIB, 3) if size is not None else None
