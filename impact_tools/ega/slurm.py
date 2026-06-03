"""SLURM planning helpers for Affiliated EGA encryption workflows."""

from __future__ import annotations

import csv
import logging
import subprocess
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


LOGGER = logging.getLogger(__name__)

BYTES_IN_GIB = 1024**3

PLAN_FIELDS = [
    "task_id",
    "sample_id",
    "file_count",
    "total_size_bytes",
    "total_size_gib",
    "chunk_file",
]

FILE_FIELDS = [
    "task_id",
    "sample_id",
    "input_file",
    "input_size_bytes",
    "input_size_gib",
]


@dataclass(frozen=True)
class SlurmEncryptionPlanConfig:
    """User-facing settings for SLURM encryption planning."""

    input_dir: Path
    recipient_pubkey: Path
    output_dir: Path | None = None
    plan_dir: Path | None = None
    crypt4gh_bin: Path | None = None
    input_list: Path | None = None
    pattern: str = "*.fastq.gz"
    task_layout: str = "sample"
    items_per_task: int = 1
    job_name: str = "localega_encrypt"
    partition: str | None = "middle_idx"
    account: str | None = None
    chdir: Path | None = None
    ntasks: int = 1
    cpus_per_task: int = 2
    mem: str = "8G"
    time_limit: str = "24:00:00"
    setup_commands: tuple[str, ...] = ()
    no_checksums: bool = False
    no_plots: bool = True
    force: bool = False
    fail_fast: bool = True


@dataclass(frozen=True)
class SlurmEncryptionPlanResult:
    """Generated plan output."""

    run_id: str
    plan_dir: Path
    chunks_dir: Path
    logs_dir: Path
    task_plan_file: Path
    file_plan_file: Path
    chunk_index_file: Path
    sbatch_file: Path
    run_file: Path
    task_count: int
    file_count: int
    total_size_bytes: int
    submitted: bool = False
    submit_stdout: str = ""
    submit_stderr: str = ""


def write_slurm_encryption_plan(
    config: SlurmEncryptionPlanConfig,
) -> SlurmEncryptionPlanResult:
    """Write input chunks, manifests and an sbatch array script."""
    input_dir = config.input_dir.expanduser().resolve()
    recipient_pubkey = config.recipient_pubkey.expanduser().resolve()
    output_dir = (
        config.output_dir.expanduser().resolve()
        if config.output_dir is not None
        else input_dir / "encrypted_c4gh"
    )
    run_id = datetime.now().strftime("%Y%m%d%H%M%S")
    plan_dir = (
        config.plan_dir.expanduser().resolve()
        if config.plan_dir is not None
        else input_dir / "slurm_encryption_plans" / run_id
    )
    chunks_dir = plan_dir / "chunks"
    logs_dir = plan_dir / "slurm_logs"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    _validate_config(config, input_dir, recipient_pubkey)
    files = _collect_input_files(
        input_dir=input_dir,
        output_dir=output_dir,
        plan_dir=plan_dir,
        pattern=config.pattern,
        input_list=config.input_list,
    )
    if not files:
        raise ValueError(f"No input files found under {input_dir} with {config.pattern}")

    tasks = _build_tasks(files, input_dir, config.task_layout, config.items_per_task)
    task_plan_file = plan_dir / f"encryption_slurm_tasks_{run_id}.tsv"
    file_plan_file = plan_dir / f"encryption_slurm_files_{run_id}.tsv"
    chunk_index_file = plan_dir / f"encryption_slurm_chunks_{run_id}.txt"
    sbatch_file = plan_dir / f"encrypt_localega_array_{run_id}.sbatch"
    run_file = plan_dir / f"_run_encrypt_localega_array_{run_id}.sh"

    task_rows = []
    file_rows = []
    chunk_paths = []
    for task_id, (sample_id, task_files) in enumerate(tasks, start=1):
        chunk_file = chunks_dir / f"task_{task_id:04d}.txt"
        chunk_file.write_text(
            "".join(f"{path}\n" for path in task_files),
            encoding="utf-8",
        )
        chunk_paths.append(chunk_file)

        total_size = sum(path.stat().st_size for path in task_files)
        task_rows.append(
            {
                "task_id": task_id,
                "sample_id": sample_id,
                "file_count": len(task_files),
                "total_size_bytes": total_size,
                "total_size_gib": f"{total_size / BYTES_IN_GIB:.6f}",
                "chunk_file": str(chunk_file),
            }
        )
        for path in task_files:
            size = path.stat().st_size
            file_rows.append(
                {
                    "task_id": task_id,
                    "sample_id": sample_id,
                    "input_file": str(path),
                    "input_size_bytes": size,
                    "input_size_gib": f"{size / BYTES_IN_GIB:.6f}",
                }
            )

    _write_tsv(task_plan_file, PLAN_FIELDS, task_rows)
    _write_tsv(file_plan_file, FILE_FIELDS, file_rows)
    chunk_index_file.write_text(
        "".join(f"{path}\n" for path in chunk_paths),
        encoding="utf-8",
    )
    _write_sbatch(
        sbatch_file=sbatch_file,
        config=config,
        input_dir=input_dir,
        output_dir=output_dir,
        recipient_pubkey=recipient_pubkey,
        chunk_index_file=chunk_index_file,
        logs_dir=logs_dir,
        task_count=len(tasks),
    )
    _write_run_script(run_file, sbatch_file)

    LOGGER.info("SLURM task plan written to %s", task_plan_file)
    LOGGER.info("SLURM file plan written to %s", file_plan_file)
    LOGGER.info("SLURM chunk index written to %s", chunk_index_file)
    LOGGER.info("SLURM array script written to %s", sbatch_file)
    LOGGER.info("SLURM run helper written to %s", run_file)

    return SlurmEncryptionPlanResult(
        run_id=run_id,
        plan_dir=plan_dir,
        chunks_dir=chunks_dir,
        logs_dir=logs_dir,
        task_plan_file=task_plan_file,
        file_plan_file=file_plan_file,
        chunk_index_file=chunk_index_file,
        sbatch_file=sbatch_file,
        run_file=run_file,
        task_count=len(tasks),
        file_count=len(files),
        total_size_bytes=sum(path.stat().st_size for path in files),
    )


def submit_slurm_job(result: SlurmEncryptionPlanResult) -> SlurmEncryptionPlanResult:
    """Submit the generated sbatch file and return the enriched result."""
    completed = subprocess.run(
        ["sbatch", str(result.sbatch_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return SlurmEncryptionPlanResult(
        run_id=result.run_id,
        plan_dir=result.plan_dir,
        chunks_dir=result.chunks_dir,
        logs_dir=result.logs_dir,
        task_plan_file=result.task_plan_file,
        file_plan_file=result.file_plan_file,
        chunk_index_file=result.chunk_index_file,
        sbatch_file=result.sbatch_file,
        run_file=result.run_file,
        task_count=result.task_count,
        file_count=result.file_count,
        total_size_bytes=result.total_size_bytes,
        submitted=completed.returncode == 0,
        submit_stdout=completed.stdout.strip(),
        submit_stderr=completed.stderr.strip(),
    )


def _validate_config(
    config: SlurmEncryptionPlanConfig,
    input_dir: Path,
    recipient_pubkey: Path,
) -> None:
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input directory does not exist: {input_dir}")
    if not recipient_pubkey.is_file():
        raise FileNotFoundError(f"Recipient public key does not exist: {recipient_pubkey}")
    if config.task_layout not in {"sample", "file"}:
        raise ValueError("--task-layout must be one of: sample, file")
    if config.items_per_task < 1:
        raise ValueError("--items-per-task must be greater than zero")
    if config.ntasks < 1:
        raise ValueError("--ntasks must be greater than zero")
    if config.cpus_per_task < 1:
        raise ValueError("--cpus-per-task must be greater than zero")


def _collect_input_files(
    input_dir: Path,
    output_dir: Path,
    plan_dir: Path,
    pattern: str,
    input_list: Path | None,
) -> list[Path]:
    if input_list is not None:
        return _read_input_list(input_list.expanduser().resolve(), input_dir, output_dir, plan_dir)

    files: list[Path] = []
    for candidate in input_dir.rglob(pattern):
        if not candidate.is_file():
            continue
        if _is_inside(candidate, output_dir) or _is_inside(candidate, plan_dir):
            continue
        files.append(candidate.resolve())
    return sorted(files)


def _read_input_list(
    input_list: Path,
    input_dir: Path,
    output_dir: Path,
    plan_dir: Path,
) -> list[Path]:
    if not input_list.is_file():
        raise FileNotFoundError(f"Input list does not exist: {input_list}")

    files: list[Path] = []
    seen: set[Path] = set()
    with input_list.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            candidate = Path(line).expanduser()
            if not candidate.is_absolute():
                candidate = input_dir / candidate
            candidate = candidate.resolve()
            if not candidate.is_file():
                raise FileNotFoundError(
                    f"Input list entry does not exist or is not a file "
                    f"({input_list}:{line_number}): {line}"
                )
            if _is_inside(candidate, output_dir) or _is_inside(candidate, plan_dir):
                raise ValueError(
                    f"Input list entry points inside generated output directories "
                    f"({input_list}:{line_number}): {candidate}"
                )
            if candidate in seen:
                LOGGER.warning(
                    "Ignoring duplicate input list entry at %s:%s: %s",
                    input_list,
                    line_number,
                    candidate,
                )
                continue
            files.append(candidate)
            seen.add(candidate)
    return files


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _build_tasks(
    files: list[Path],
    input_dir: Path,
    task_layout: str,
    items_per_task: int,
) -> list[tuple[str, list[Path]]]:
    if task_layout == "file":
        file_groups = [(path.stem, [path]) for path in files]
        return _chunk_groups(file_groups, items_per_task, "files")

    grouped: dict[str, list[Path]] = {}
    for path in files:
        sample_id = _sample_id_for_file(path, input_dir)
        grouped.setdefault(sample_id, []).append(path)
    sample_groups = [(sample_id, sorted(paths)) for sample_id, paths in sorted(grouped.items())]
    return _chunk_groups(sample_groups, items_per_task, "samples")


def _sample_id_for_file(input_file: Path, input_dir: Path) -> str:
    relative_parent = input_file.relative_to(input_dir).parent
    if relative_parent == Path("."):
        return input_dir.name
    return str(relative_parent)


def _chunk_groups(
    groups: list[tuple[str, list[Path]]],
    items_per_task: int,
    label: str,
) -> list[tuple[str, list[Path]]]:
    if items_per_task == 1:
        return groups

    tasks: list[tuple[str, list[Path]]] = []
    for start in range(0, len(groups), items_per_task):
        chunk = groups[start : start + items_per_task]
        chunk_label = f"{label}_{(start // items_per_task) + 1:04d}"
        chunk_files: list[Path] = []
        for _, paths in chunk:
            chunk_files.extend(paths)
        tasks.append((chunk_label, sorted(chunk_files)))
    return tasks


def _write_tsv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_sbatch(
    sbatch_file: Path,
    config: SlurmEncryptionPlanConfig,
    input_dir: Path,
    output_dir: Path,
    recipient_pubkey: Path,
    chunk_index_file: Path,
    logs_dir: Path,
    task_count: int,
) -> None:
    command = [
        "impact-tools",
        "ega",
        "encrypt",
        "--input-dir",
        str(input_dir),
        "--input-list",
        '"${CHUNK_FILE}"',
        "--recipient-pubkey",
        str(recipient_pubkey),
        "--output-dir",
        str(output_dir),
    ]
    if config.crypt4gh_bin is not None:
        command.extend(["--crypt4gh-bin", str(config.crypt4gh_bin.expanduser().resolve())])
    if config.no_checksums:
        command.append("--no-checksums")
    if config.no_plots:
        command.append("--no-plots")
    if config.force:
        command.append("--force")
    if config.fail_fast:
        command.append("--fail-fast")

    safe_command = " \\\n  ".join(
        part if part == '"${CHUNK_FILE}"' else shlex.quote(part) for part in command
    )

    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={config.job_name}",
        f"#SBATCH --array=1-{task_count}",
        f"#SBATCH --ntasks={config.ntasks}",
        f"#SBATCH --cpus-per-task={config.cpus_per_task}",
        f"#SBATCH --mem={config.mem}",
        f"#SBATCH --time={config.time_limit}",
        f"#SBATCH --output={logs_dir}/%x_%A_%a.out",
        f"#SBATCH --error={logs_dir}/%x_%A_%a.err",
    ]
    if config.partition is not None:
        lines.append(f"#SBATCH --partition={config.partition}")
    if config.account is not None:
        lines.append(f"#SBATCH --account={config.account}")
    if config.chdir is not None:
        lines.append(f"#SBATCH --chdir={config.chdir.expanduser().resolve()}")
    lines.extend(
        [
            "",
            "set -euo pipefail",
            "",
        ]
    )
    if config.setup_commands:
        lines.append("# Environment setup")
        lines.extend(config.setup_commands)
        lines.append("")
    lines.extend(
        [
            f"CHUNK_INDEX={shlex.quote(str(chunk_index_file))}",
            'CHUNK_FILE=$(sed -n "${SLURM_ARRAY_TASK_ID}p" "${CHUNK_INDEX}")',
            "",
            'if [[ -z "${CHUNK_FILE}" || ! -f "${CHUNK_FILE}" ]]; then',
            '  echo "Missing chunk file for task ${SLURM_ARRAY_TASK_ID}: ${CHUNK_FILE}" >&2',
            "  exit 1",
            "fi",
            "",
            'echo "Running LocalEGA encryption task ${SLURM_ARRAY_TASK_ID}"',
            'echo "Chunk file: ${CHUNK_FILE}"',
            "",
            safe_command,
            "",
        ]
    )
    sbatch_file.write_text("\n".join(lines), encoding="utf-8")


def _write_run_script(run_file: Path, sbatch_file: Path) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"sbatch {shlex.quote(str(sbatch_file))}",
        "",
    ]
    run_file.write_text("\n".join(lines), encoding="utf-8")
    run_file.chmod(0o755)
