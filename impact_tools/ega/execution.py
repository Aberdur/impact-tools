"""Execution-environment metadata for comparable EGA runs."""

from __future__ import annotations

import os
import platform
import resource
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter


@dataclass(frozen=True)
class ExecutionEnvironment:
    """Runtime metadata recorded in EGA manifests."""

    profile: str
    hostname: str
    platform: str
    python_version: str
    cpu_count: int | None
    cpu_model: str | None
    memory_bytes: int | None
    slurm_job_id: str | None
    slurm_array_job_id: str | None
    slurm_array_task_id: str | None
    slurm_partition: str | None
    slurm_cpus_per_task: str | None
    slurm_mem_per_node: str | None
    captured_at: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProcessSnapshot:
    wall_time: float
    user_seconds: float
    system_seconds: float
    read_bytes: int | None
    write_bytes: int | None


@dataclass(frozen=True)
class ProcessMetrics:
    wall_seconds: float
    user_seconds: float
    system_seconds: float
    max_rss_mib: float
    read_bytes: int | None
    write_bytes: int | None


def make_run_id() -> str:
    """Return a collision-resistant run identifier for concurrent jobs."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    task_id = os.environ.get("SLURM_ARRAY_TASK_ID")
    return f"{timestamp}_task{task_id}" if task_id else timestamp


def collect_execution_environment(profile: str) -> ExecutionEnvironment:
    """Collect stable host and scheduler metadata for one run."""
    return ExecutionEnvironment(
        profile=profile,
        hostname=socket.gethostname(),
        platform=platform.platform(),
        python_version=platform.python_version(),
        cpu_count=os.cpu_count(),
        cpu_model=_cpu_model(),
        memory_bytes=_memory_bytes(),
        slurm_job_id=os.environ.get("SLURM_JOB_ID"),
        slurm_array_job_id=os.environ.get("SLURM_ARRAY_JOB_ID"),
        slurm_array_task_id=os.environ.get("SLURM_ARRAY_TASK_ID"),
        slurm_partition=os.environ.get("SLURM_JOB_PARTITION"),
        slurm_cpus_per_task=os.environ.get("SLURM_CPUS_PER_TASK"),
        slurm_mem_per_node=os.environ.get("SLURM_MEM_PER_NODE"),
        captured_at=datetime.now(timezone.utc).isoformat(),
    )


def start_process_metrics() -> ProcessSnapshot:
    usage = _resource_usage()
    io = _proc_io()
    return ProcessSnapshot(
        wall_time=perf_counter(),
        user_seconds=usage["user_seconds"],
        system_seconds=usage["system_seconds"],
        read_bytes=io.get("read_bytes"),
        write_bytes=io.get("write_bytes"),
    )


def finish_process_metrics(start: ProcessSnapshot) -> ProcessMetrics:
    usage = _resource_usage()
    io = _proc_io()
    return ProcessMetrics(
        wall_seconds=round(perf_counter() - start.wall_time, 6),
        user_seconds=round(usage["user_seconds"] - start.user_seconds, 6),
        system_seconds=round(usage["system_seconds"] - start.system_seconds, 6),
        max_rss_mib=round(usage["max_rss_kib"] / 1024, 3),
        read_bytes=_difference(io.get("read_bytes"), start.read_bytes),
        write_bytes=_difference(io.get("write_bytes"), start.write_bytes),
    )


def _memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None


def _cpu_model() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return platform.processor() or None


def _proc_io() -> dict[str, int]:
    try:
        lines = Path("/proc/self/io").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    return {
        key: int(value.strip())
        for line in lines
        for key, value in [line.split(":", 1)]
        if key in {"read_bytes", "write_bytes"}
    }


def _resource_usage() -> dict[str, float]:
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "user_seconds": own.ru_utime + children.ru_utime,
        "system_seconds": own.ru_stime + children.ru_stime,
        "max_rss_kib": max(own.ru_maxrss, children.ru_maxrss),
    }


def _difference(current: int | None, initial: int | None) -> int | None:
    if current is None or initial is None:
        return None
    return max(0, current - initial)
