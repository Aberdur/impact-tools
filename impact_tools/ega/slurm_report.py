"""Aggregate per-task SLURM encryption manifests into one logical run."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from impact_tools.ega.encrypt import METRIC_FIELDS
from impact_tools.ega.html_report import write_encryption_report


@dataclass(frozen=True)
class SlurmEncryptionReportResult:
    """Artifacts written for one completed SLURM encryption array."""

    output_dir: Path
    manifest_file: Path
    metrics_file: Path
    summary_file: Path
    report_file: Path
    task_manifests: int
    expected_tasks: int
    files: int
    failed: int


def aggregate_slurm_encryption(
    *,
    manifest_dir: Path,
    output_dir: Path,
    array_job_ids: tuple[str, ...],
    expected_tasks: int,
    include_charts: bool = True,
) -> SlurmEncryptionReportResult:
    """Combine task manifests belonging to a SLURM array into one report."""
    source_dir = manifest_dir.expanduser().resolve()
    resolved_output = output_dir.expanduser().resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)

    job_ids = tuple(str(job_id) for job_id in array_job_ids)
    if not job_ids:
        raise ValueError("At least one SLURM array job ID is required")
    allowed_job_ids = set(job_ids)

    payloads = []
    for path in sorted(source_dir.glob("encryption_manifest_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        execution = payload.get("execution", {})
        if str(execution.get("slurm_array_job_id") or "") in allowed_job_ids:
            payloads.append((path, payload))

    if not payloads:
        raise ValueError(
            f"No encryption manifests found for SLURM array job(s) {', '.join(job_ids)} "
            f"under {source_dir}"
        )

    files = [record for _, payload in payloads for record in payload.get("files", [])]
    summaries = [payload["summary"] for _, payload in payloads]
    missing_tasks = max(expected_tasks - len(payloads), 0)
    failed = sum(int(summary.get("failed", 0)) for summary in summaries)

    starts = []
    ends = []
    for _, payload in payloads:
        execution = payload["execution"]
        end = datetime.fromisoformat(execution["captured_at"])
        wall = float(payload["summary"]["process"]["wall_seconds"])
        ends.append(end)
        starts.append(end - timedelta(seconds=wall))
    array_wall_seconds = (max(ends) - min(starts)).total_seconds()

    first = payloads[0][1]
    execution = dict(first["execution"])
    execution.update(
        hostname=", ".join(
            sorted({payload["execution"]["hostname"] for _, payload in payloads})
        ),
        slurm_job_id=",".join(job_ids),
        slurm_array_job_id=",".join(job_ids),
        slurm_array_task_id=None,
    )
    process = {
        "wall_seconds": round(array_wall_seconds, 6),
        "user_seconds": round(
            sum(float(summary["process"]["user_seconds"]) for summary in summaries), 6
        ),
        "system_seconds": round(
            sum(float(summary["process"]["system_seconds"]) for summary in summaries), 6
        ),
        "max_rss_mib": max(
            float(summary["process"]["max_rss_mib"]) for summary in summaries
        ),
        "read_bytes": _sum_optional(summary["process"].get("read_bytes") for summary in summaries),
        "write_bytes": _sum_optional(
            summary["process"].get("write_bytes") for summary in summaries
        ),
    }
    worker_encryption_seconds = sum(
        float(summary.get("total_encryption_seconds", 0)) for summary in summaries
    )
    processed_input_bytes = sum(
        int(summary.get("processed_input_bytes", 0)) for summary in summaries
    )
    job_tag = "_".join(job_ids)
    run_id = f"slurm_array_{job_tag}"
    aggregate = {
        "execution": execution,
        "run": {
            **first["run"],
            "run_id": run_id,
            "input_list": None,
        },
        "array": {
            "job_ids": list(job_ids),
            "expected_tasks": expected_tasks,
            "task_manifests": len(payloads),
            "missing_tasks": missing_tasks,
            "task_run_ids": [payload["run"]["run_id"] for _, payload in payloads],
            "source_manifests": [str(path) for path, _ in payloads],
        },
        "summary": {
            "files_discovered": sum(
                int(summary.get("files_discovered", 0)) for summary in summaries
            ),
            "encrypted": sum(int(summary.get("encrypted", 0)) for summary in summaries),
            "skipped": sum(int(summary.get("skipped", 0)) for summary in summaries),
            "failed": failed,
            "total_input_bytes": sum(
                int(summary.get("total_input_bytes", 0)) for summary in summaries
            ),
            "processed_input_bytes": processed_input_bytes,
            "total_output_bytes": sum(
                int(summary.get("total_output_bytes", 0)) for summary in summaries
            ),
            "total_encryption_seconds": round(array_wall_seconds, 6),
            "worker_encryption_seconds_sum": round(worker_encryption_seconds, 6),
            "process": process,
        },
        "files": files,
    }

    manifest_file = resolved_output / f"encryption_array_manifest_{job_tag}.json"
    metrics_file = resolved_output / f"encryption_array_metrics_{job_tag}.tsv"
    summary_file = resolved_output / f"encryption_array_summary_{job_tag}.txt"
    report_file = resolved_output / f"encryption_array_report_{job_tag}.html"
    aggregate.update(
        manifest_file=str(manifest_file),
        metrics_file=str(metrics_file),
        summary_file=str(summary_file),
    )

    manifest_file.write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
    with metrics_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=METRIC_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(
            {field: record.get(field, "") for field in METRIC_FIELDS}
            for record in files
        )
    summary_file.write_text(
        "\n".join(
            [
                f"SLURM array jobs: {', '.join(job_ids)}",
                f"Expected tasks: {expected_tasks}",
                f"Task manifests: {len(payloads)}",
                f"Missing tasks: {missing_tasks}",
                f"Files discovered: {aggregate['summary']['files_discovered']}",
                f"Encrypted: {aggregate['summary']['encrypted']}",
                f"Skipped: {aggregate['summary']['skipped']}",
                f"Failed: {failed}",
                f"Array wall seconds: {array_wall_seconds:.6f}",
                f"Worker encryption seconds sum: {worker_encryption_seconds:.6f}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_encryption_report(report_file, aggregate, include_charts=include_charts)
    return SlurmEncryptionReportResult(
        output_dir=resolved_output,
        manifest_file=manifest_file,
        metrics_file=metrics_file,
        summary_file=summary_file,
        report_file=report_file,
        task_manifests=len(payloads),
        expected_tasks=expected_tasks,
        files=len(files),
        failed=failed + missing_tasks,
    )


def _sum_optional(values) -> int | None:
    present = [int(value) for value in values if value is not None]
    return sum(present) if present else None
