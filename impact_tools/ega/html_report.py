"""Self-contained HTML reports for EGA execution metrics."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape


COLORS = ["#2563eb", "#0891b2", "#7c3aed", "#db2777", "#ea580c", "#16a34a"]
RESOURCE_PACKAGE = "impact_tools.ega.resources"

TEMPLATE_ENVIRONMENT = Environment(
    loader=PackageLoader("impact_tools.ega", "resources"),
    autoescape=select_autoescape(("html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)


def write_encryption_report(
    path: Path,
    payload: dict,
    include_charts: bool = True,
) -> None:
    """Write one visual report for a standalone Crypt4GH encryption run."""
    execution = payload["execution"]
    run = payload["run"]
    summary = payload["summary"]
    records = payload.get("files", [])
    array = payload.get("array")
    throughput = _throughput(
        summary["processed_input_bytes"],
        summary["total_encryption_seconds"],
    )
    context = _base_context(
        page_title="EGA encryption report",
        report_title=(
            "Crypt4GH encryption array run" if array else "Crypt4GH encryption run"
        ),
        badge=execution["profile"],
        summary_cards=[
            _card("Profile", execution["profile"], execution["hostname"]),
            _card("Input", _gib(summary["total_input_bytes"]), "GiB discovered"),
            _card("Encrypted", summary["encrypted"], "files"),
            _card("Skipped", summary["skipped"], "files"),
            _card("Failed", summary["failed"], "files"),
            _card("Throughput", _rate(throughput), "MiB/s"),
            _card(
                "Encryption",
                _duration(summary["total_encryption_seconds"]),
                "operation time",
            ),
            _card(
                "Peak memory",
                f"{summary['process']['max_rss_mib']:.1f}",
                "MiB observed RSS",
            ),
        ]
        + (
            [
                _card(
                    "Array tasks",
                    f"{array['task_manifests']}/{array['expected_tasks']}",
                    "task manifests",
                ),
                _card("Missing tasks", array["missing_tasks"], "tasks"),
            ]
            if array
            else []
        ),
        performance_charts=(
            [
                _chart(
                    "Throughput by file",
                    "MiB/s",
                    [_record_label(record) for record in records],
                    [record.get("throughput_mib_s") or 0 for record in records],
                ),
                _chart(
                    "Encryption duration by file",
                    "seconds",
                    [_record_label(record) for record in records],
                    [record.get("encryption_seconds") or 0 for record in records],
                ),
                _chart(
                    "Input size by file",
                    "GiB",
                    [_record_label(record) for record in records],
                    [record.get("input_size_gib") or 0 for record in records],
                ),
            ]
            if include_charts
            else []
        ),
    )
    context.update(
        environment=_items(
            [
                ("Run ID", run["run_id"]),
                ("Host", execution["hostname"]),
                ("CPU", execution.get("cpu_model")),
                ("Logical CPUs", execution.get("cpu_count")),
                ("System memory", _gib(execution.get("memory_bytes")) + " GiB"),
                ("SLURM job", execution.get("slurm_job_id")),
                ("SLURM array task", execution.get("slurm_array_task_id")),
                (
                    "SLURM array tasks",
                    f"{array['task_manifests']}/{array['expected_tasks']}"
                    if array
                    else None,
                ),
                ("SLURM missing tasks", array.get("missing_tasks") if array else None),
                ("SLURM partition", execution.get("slurm_partition")),
                ("CPUs per task", execution.get("slurm_cpus_per_task")),
                ("SLURM memory", execution.get("slurm_mem_per_node")),
                ("Python", execution.get("python_version")),
                ("Input directory", run.get("input_dir")),
                ("Output directory", run.get("output_dir")),
                ("Crypt4GH executable", run.get("crypt4gh_bin")),
            ]
        ),
        encryption_headers=[
            "Sample",
            "Input",
            "Output",
            "Input GiB",
            "Output GiB",
            "Seconds",
            "MiB/s",
            "Overhead %",
            "Status",
        ],
        encryption_rows=[
            _table_row(
                [
                    record.get("sample_id"),
                    record.get("input_file"),
                    record.get("output_file"),
                    record.get("input_size_gib"),
                    record.get("output_size_gib"),
                    record.get("encryption_seconds"),
                    record.get("throughput_mib_s"),
                    record.get("overhead_percent"),
                    record.get("status"),
                ],
                path_columns={1, 2},
                status_column=8,
            )
            for record in records
        ],
        artifacts=[
            {"label": label, "path": target}
            for label, target in [
                ("Manifest", payload.get("manifest_file")),
                ("Metrics", payload.get("metrics_file")),
                ("Summary", payload.get("summary_file")),
                ("Execution log", payload.get("log_file")),
            ]
            if target
        ],
    )
    _render(path, "encryption_report.html", context)


def write_upload_report(
    path: Path,
    payload: dict,
    include_charts: bool = True,
) -> None:
    """Write one visual report for a standalone Inbox upload run."""
    execution = payload["execution"]
    run = payload["run"]
    summary = payload["summary"]
    records = payload.get("files", [])
    throughput = _throughput(
        summary["uploaded_input_bytes"],
        summary["total_upload_seconds"],
    )
    context = _base_context(
        page_title="EGA Inbox upload report",
        report_title="LocalEGA Inbox upload run",
        badge=execution["profile"],
        summary_cards=[
            _card("Profile", execution["profile"], execution["hostname"]),
            _card("Input", _gib(summary["total_input_bytes"]), "GiB discovered"),
            _card("Uploaded", summary["uploaded"], "files"),
            _card("Skipped", summary["skipped"], "files"),
            _card("Failed", summary["failed"], "files"),
            _card("Throughput", _rate(throughput), "MiB/s"),
            _card(
                "Upload",
                _duration(summary["total_upload_seconds"]),
                "operation time",
            ),
            _card(
                "Peak memory",
                f"{summary['process']['max_rss_mib']:.1f}",
                "MiB observed RSS",
            ),
        ],
        performance_charts=(
            [
                _chart(
                    "Throughput by file",
                    "MiB/s",
                    [_upload_record_label(record) for record in records],
                    [record.get("throughput_mib_s") or 0 for record in records],
                ),
                _chart(
                    "Upload duration by file",
                    "seconds",
                    [_upload_record_label(record) for record in records],
                    [record.get("upload_seconds") or 0 for record in records],
                ),
                _chart(
                    "Input size by file",
                    "GiB",
                    [_upload_record_label(record) for record in records],
                    [record.get("local_size_gib") or 0 for record in records],
                ),
            ]
            if include_charts
            else []
        ),
    )
    context.update(
        environment=_items(
            [
                ("Run ID", run["run_id"]),
                ("Host", execution["hostname"]),
                ("CPU", execution.get("cpu_model")),
                ("Logical CPUs", execution.get("cpu_count")),
                ("System memory", _gib(execution.get("memory_bytes")) + " GiB"),
                ("SLURM job", execution.get("slurm_job_id")),
                ("SLURM array task", execution.get("slurm_array_task_id")),
                ("SLURM partition", execution.get("slurm_partition")),
                ("CPUs per task", execution.get("slurm_cpus_per_task")),
                ("SLURM memory", execution.get("slurm_mem_per_node")),
                ("Python", execution.get("python_version")),
                ("Input directory", run.get("input_dir")),
                ("Report directory", run.get("output_dir")),
                (
                    "Inbox endpoint",
                    f"{run.get('username')}@{run.get('host')}:{run.get('port')}",
                ),
                ("Remote directory", run.get("remote_dir")),
                ("Remote layout", run.get("remote_layout")),
            ]
        ),
        upload_headers=[
            "Sample",
            "Local file",
            "Remote path",
            "Input GiB",
            "Seconds",
            "MiB/s",
            "Status",
        ],
        upload_rows=[
            _table_row(
                [
                    record.get("sample_id"),
                    record.get("local_file"),
                    record.get("remote_path"),
                    record.get("local_size_gib"),
                    record.get("upload_seconds"),
                    record.get("throughput_mib_s"),
                    record.get("status"),
                ],
                path_columns={1, 2},
                status_column=6,
            )
            for record in records
        ],
        artifacts=[
            {"label": label, "path": target}
            for label, target in [
                ("Manifest", payload.get("manifest_file")),
                ("Metrics", payload.get("metrics_file")),
                ("Summary", payload.get("summary_file")),
                ("Execution log", payload.get("log_file")),
            ]
            if target
        ],
    )
    _render(path, "upload_report.html", context)


def write_workflow_report(
    path: Path,
    payload: dict,
    encryption_files: list[dict],
    upload_files: list[dict],
    include_charts: bool = True,
) -> None:
    """Write one visual report for a completed encrypt-and-upload workflow."""
    execution = payload["execution"]
    encryption = payload["encryption"]
    upload = payload["upload"]
    encryption_rate = _throughput(
        encryption["processed_input_bytes"],
        encryption["total_encryption_seconds"],
    )
    upload_rate = _throughput(
        upload["uploaded_input_bytes"],
        upload["total_upload_seconds"],
    )
    peak_memory = max(
        encryption["process"]["max_rss_mib"],
        upload["process"]["max_rss_mib"],
    )
    context = _base_context(
        page_title="EGA workflow report",
        report_title="EGA encryption and Inbox upload",
        badge=execution["profile"],
        summary_cards=[
            _card("Profile", execution["profile"], execution["hostname"]),
            _card("Input", _gib(encryption["total_input_bytes"]), "GiB discovered"),
            _card("Encrypted", encryption["encrypted"], "files"),
            _card("Uploaded", upload["uploaded"], "files"),
            _card("Encryption", _rate(encryption_rate), "MiB/s"),
            _card("Upload", _rate(upload_rate), "MiB/s"),
            _card("End to end", _duration(payload["wall_seconds"]), "wall time"),
            _card("Peak memory", f"{peak_memory:.1f}", "MiB RSS"),
        ],
        performance_charts=(
            [
                _chart(
                    "Throughput",
                    "MiB/s",
                    ["Encryption", "Upload"],
                    [encryption_rate or 0, upload_rate or 0],
                ),
                _chart(
                    "Stage duration",
                    "seconds",
                    ["Encryption", "Upload", "Total"],
                    [
                        encryption["process"]["wall_seconds"],
                        upload["process"]["wall_seconds"],
                        payload["wall_seconds"],
                    ],
                ),
            ]
            if include_charts
            else []
        ),
    )
    context.update(
        environment=_items(
            [
                ("Run ID", payload["run_id"]),
                ("Host", execution["hostname"]),
                ("CPU", execution.get("cpu_model")),
                ("Logical CPUs", execution.get("cpu_count")),
                ("System memory", _gib(execution.get("memory_bytes")) + " GiB"),
                ("SLURM job", execution.get("slurm_job_id")),
                ("SLURM partition", execution.get("slurm_partition")),
                ("CPUs per task", execution.get("slurm_cpus_per_task")),
                ("SLURM memory", execution.get("slurm_mem_per_node")),
                ("Python", execution.get("python_version")),
            ]
        ),
        encryption_headers=[
            "Sample",
            "Input",
            "Output",
            "GiB",
            "Seconds",
            "MiB/s",
            "Status",
        ],
        encryption_rows=[
            _table_row(
                [
                    record.get("sample_id"),
                    record.get("input_file"),
                    record.get("output_file"),
                    record.get("input_size_gib"),
                    record.get("encryption_seconds"),
                    record.get("throughput_mib_s"),
                    record.get("status"),
                ],
                path_columns={1, 2},
                status_column=6,
            )
            for record in encryption_files
        ],
        upload_headers=[
            "Sample",
            "Local file",
            "Remote path",
            "GiB",
            "Seconds",
            "MiB/s",
            "Status",
        ],
        upload_rows=[
            _table_row(
                [
                    record.get("sample_id"),
                    record.get("local_file"),
                    record.get("remote_path"),
                    record.get("local_size_gib"),
                    record.get("upload_seconds"),
                    record.get("throughput_mib_s"),
                    record.get("status"),
                ],
                path_columns={1, 2},
                status_column=6,
            )
            for record in upload_files
        ],
        artifacts=[
            {"label": label, "path": target}
            for label, target in [
                ("Encryption manifest", encryption.get("manifest_file")),
                ("Encryption metrics", encryption.get("metrics_file")),
                ("Upload manifest", upload.get("manifest_file")),
                ("Upload metrics", upload.get("metrics_file")),
                ("Upload input list", payload.get("upload_input_list")),
                ("Upload source tree", payload.get("upload_source_dir")),
            ]
            if target
        ],
    )
    _render(path, "workflow_report.html", context)


def write_comparison_report(
    path: Path,
    rows: list[dict],
    include_charts: bool = True,
) -> None:
    """Write a visual comparison for standalone and end-to-end runs."""
    run_types = sorted({row["run_type"] for row in rows})
    fastest_encryption = _best(rows, "encryption_mib_s", highest=True)
    fastest_upload = _best(rows, "upload_mib_s", highest=True)
    fastest_stage = _best(rows, "stage_wall_seconds", highest=False)
    workflow_rows = [row for row in rows if row["run_type"] == "workflow"]
    fastest_workflow = _best(workflow_rows, "wall_seconds", highest=False)
    summary_cards = [
        _card("Runs", len(rows), "compared"),
        _card("Run types", len(run_types), ", ".join(run_types)),
    ]
    if fastest_encryption:
        summary_cards.append(
            _card(
                "Best encryption",
                _rate(fastest_encryption["encryption_mib_s"]),
                _comparison_label(fastest_encryption),
            )
        )
    if fastest_upload:
        summary_cards.append(
            _card(
                "Best upload",
                _rate(fastest_upload["upload_mib_s"]),
                _comparison_label(fastest_upload),
            )
        )
    if fastest_workflow:
        summary_cards.append(
            _card(
                "Fastest end to end",
                _duration(fastest_workflow["wall_seconds"]),
                _comparison_label(fastest_workflow),
            )
        )
    elif fastest_stage:
        summary_cards.append(
            _card(
                "Fastest stage",
                _duration(fastest_stage["stage_wall_seconds"]),
                _comparison_label(fastest_stage),
            )
        )

    charts = []
    if include_charts:
        charts = [
            chart
            for chart in [
                _metric_chart(
                    "Encryption throughput",
                    "MiB/s",
                    rows,
                    "encryption_mib_s",
                ),
                _metric_chart(
                    "Inbox upload throughput",
                    "MiB/s",
                    rows,
                    "upload_mib_s",
                ),
                _metric_chart(
                    "Stage runtime",
                    "seconds",
                    rows,
                    "stage_wall_seconds",
                ),
                _metric_chart(
                    "End-to-end runtime",
                    "seconds",
                    workflow_rows,
                    "wall_seconds",
                ),
                _metric_chart(
                    "Peak memory",
                    "MiB observed RSS",
                    rows,
                    "max_rss_mib",
                ),
            ]
            if chart is not None
        ]
    context = _base_context(
        page_title="EGA performance comparison",
        report_title="EGA execution performance comparison",
        badge=" · ".join(run_types),
        summary_cards=summary_cards,
        performance_charts=charts,
    )
    context.update(
        comparison_headers=[
            "Type",
            "Profile",
            "Host",
            "Input GiB",
            "Encryption MiB/s",
            "Upload MiB/s",
            "Stage wall",
            "End to end",
            "Peak RSS MiB",
            "CPU",
            "SLURM job",
        ],
        comparison_rows=[
            _table_row(
                [
                    row["run_type"],
                    row["profile"],
                    row["hostname"],
                    row["input_gib"],
                    row["encryption_mib_s"],
                    row["upload_mib_s"],
                    _duration(row["stage_wall_seconds"]),
                    (
                        _duration(row["wall_seconds"])
                        if row["run_type"] == "workflow"
                        else None
                    ),
                    row["max_rss_mib"],
                    row["cpu_model"],
                    row["slurm_job_id"],
                ]
            )
            for row in rows
        ],
    )
    _render(path, "comparison_report.html", context)


def _base_context(**values) -> dict:
    styles = files(RESOURCE_PACKAGE).joinpath("report.css").read_text(encoding="utf-8")
    return {"styles": styles, **values}


def _render(path: Path, template_name: str, context: dict) -> None:
    rendered = TEMPLATE_ENVIRONMENT.get_template(template_name).render(**context)
    path.write_text(rendered, encoding="utf-8")


def _card(label: str, value: object, note: str) -> dict:
    return {"label": label, "value": value, "note": note}


def _items(values: list[tuple[str, object]]) -> list[dict]:
    return [
        {"label": label, "value": value}
        for label, value in values
        if value not in (None, "")
    ]


def _chart(
    title: str,
    unit: str,
    labels: list[str],
    values: list[float],
) -> dict:
    maximum = max(values, default=0) or 1
    return {
        "title": title,
        "unit": unit,
        "rows": [
            {
                "label": label,
                "value": value,
                "display": _number(value),
                "width": f"{max(0, min(100, (value / maximum) * 100)):.2f}",
                "color": COLORS[index % len(COLORS)],
            }
            for index, (label, value) in enumerate(zip(labels, values))
        ],
    }


def _metric_chart(
    title: str,
    unit: str,
    rows: list[dict],
    key: str,
) -> dict | None:
    available = [row for row in rows if row.get(key) is not None]
    if not available:
        return None
    return _chart(
        title,
        unit,
        [_comparison_label(row) for row in available],
        [row[key] for row in available],
    )


def _table_row(
    values: list,
    path_columns: set[int] | None = None,
    status_column: int | None = None,
) -> list[dict]:
    path_columns = path_columns or set()
    cells = []
    for index, value in enumerate(values):
        classes = []
        if index in path_columns:
            classes.append("path")
        if index == status_column:
            classes.extend(["status", f"status-{_status_class(value)}"])
        cells.append(
            {
                "value": _display(value),
                "muted": value is None or value == "",
                "css_class": " ".join(classes),
                "title": value if index in path_columns else None,
            }
        )
    return cells


def _best(rows: list[dict], key: str, highest: bool) -> dict:
    candidates = [row for row in rows if row.get(key) is not None]
    if not candidates:
        return {}
    return (max if highest else min)(candidates, key=lambda row: row[key])


def _run_label(row: dict) -> str:
    if not row:
        return "NA"
    return f"{row.get('profile', 'NA')} · {row.get('hostname', 'NA')}"


def _comparison_label(row: dict) -> str:
    if not row:
        return "NA"
    return (
        f"{row.get('run_type', 'run')} · "
        f"{row.get('profile', 'NA')} · {row.get('hostname', 'NA')}"
    )


def _record_label(record: dict) -> str:
    return Path(record.get("input_file", "file")).name


def _upload_record_label(record: dict) -> str:
    return Path(record.get("local_file", "file")).name


def _throughput(size: int, seconds: float) -> float | None:
    return (size / (1024**2)) / seconds if seconds > 0 else None


def _gib(size: int | None) -> str:
    return f"{size / (1024**3):.2f}" if size is not None else "NA"


def _rate(value: float | None) -> str:
    return _number(value) if value is not None else "NA"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "NA"
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:.0f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m"


def _display(value: object) -> object:
    if isinstance(value, float):
        return f"{value:.3f}"
    return value


def _number(value: float) -> str:
    if value == 0:
        return "0"
    if abs(value) < 0.01:
        return f"{value:.4f}"
    if abs(value) < 1:
        return f"{value:.3f}"
    return f"{value:.2f}"


def _status_class(value: object) -> str:
    return "ok" if value in {"ok", "skipped_existing", "skipped_registered"} else "failed"
