"""Persistent content registry for Affiliated EGA processing."""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_REGISTRY_PATH = Path(
    os.environ.get(
        "IMPACT_TOOLS_EGA_REGISTRY",
        Path.home() / ".impact_tools" / "ega_registry.sqlite3",
    )
).expanduser()


@dataclass(frozen=True)
class UploadRecord:
    """Successful upload previously recorded for encrypted content."""

    sha256: str
    endpoint: str
    remote_path: str
    local_path: str
    sample_id: str
    run_id: str
    completed_at: str


@dataclass(frozen=True)
class EncryptionRecord:
    """Successful encryption previously recorded for raw content."""

    source_sha256: str
    recipient_sha256: str
    encrypted_sha256: str
    encrypted_size: int
    input_path: str
    output_path: str
    sample_id: str
    run_id: str
    completed_at: str


class EgaRegistry:
    """SQLite-backed registry keyed by file content and Inbox endpoint."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=60)
        self.path.chmod(0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=60000")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS encryptions (
                source_sha256 TEXT NOT NULL,
                recipient_sha256 TEXT NOT NULL,
                encrypted_sha256 TEXT NOT NULL,
                encrypted_size INTEGER NOT NULL,
                input_path TEXT NOT NULL,
                output_path TEXT NOT NULL,
                sample_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                PRIMARY KEY (source_sha256, recipient_sha256)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS inbox_uploads (
                sha256 TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                remote_path TEXT NOT NULL,
                local_path TEXT NOT NULL,
                sample_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                PRIMARY KEY (sha256, endpoint)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processing_claims (
                stage TEXT NOT NULL,
                content_key TEXT NOT NULL,
                owner TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                PRIMARY KEY (stage, content_key)
            )
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> EgaRegistry:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def find_upload(self, sha256: str, endpoint: str) -> UploadRecord | None:
        row = self.connection.execute(
            """
            SELECT sha256, endpoint, remote_path, local_path, sample_id,
                   run_id, completed_at
            FROM inbox_uploads
            WHERE sha256 = ? AND endpoint = ?
            """,
            (sha256, endpoint),
        ).fetchone()
        return UploadRecord(**dict(row)) if row is not None else None

    def find_encryption(
        self,
        source_sha256: str,
        recipient_sha256: str,
    ) -> EncryptionRecord | None:
        row = self.connection.execute(
            """
            SELECT source_sha256, recipient_sha256, encrypted_sha256,
                   encrypted_size, input_path, output_path, sample_id,
                   run_id, completed_at
            FROM encryptions
            WHERE source_sha256 = ? AND recipient_sha256 = ?
            """,
            (source_sha256, recipient_sha256),
        ).fetchone()
        return EncryptionRecord(**dict(row)) if row is not None else None

    def try_claim(
        self,
        stage: str,
        content_key: str,
        *,
        stale_after: timedelta = timedelta(days=1),
    ) -> str | None:
        """Atomically reserve content processing and return its owner token."""
        now = datetime.now(timezone.utc)
        owner = uuid.uuid4().hex
        with self.connection:
            self.connection.execute(
                """
                DELETE FROM processing_claims
                WHERE claimed_at < ?
                """,
                ((now - stale_after).isoformat(),),
            )
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO processing_claims (
                    stage, content_key, owner, claimed_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (stage, content_key, owner, now.isoformat()),
            )
        return owner if cursor.rowcount == 1 else None

    def release_claim(self, stage: str, content_key: str, owner: str) -> None:
        """Release a reservation only when it belongs to the caller."""
        with self.connection:
            self.connection.execute(
                """
                DELETE FROM processing_claims
                WHERE stage = ? AND content_key = ? AND owner = ?
                """,
                (stage, content_key, owner),
            )

    def record_encryption(
        self,
        *,
        source_sha256: str,
        recipient_sha256: str,
        encrypted_sha256: str,
        encrypted_size: int,
        input_path: Path,
        output_path: Path,
        sample_id: str,
        run_id: str,
    ) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO encryptions (
                    source_sha256, recipient_sha256, encrypted_sha256,
                    encrypted_size, input_path, output_path, sample_id,
                    run_id, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_sha256, recipient_sha256) DO UPDATE SET
                    encrypted_sha256 = excluded.encrypted_sha256,
                    encrypted_size = excluded.encrypted_size,
                    input_path = excluded.input_path,
                    output_path = excluded.output_path,
                    sample_id = excluded.sample_id,
                    run_id = excluded.run_id,
                    completed_at = excluded.completed_at
                """,
                (
                    source_sha256,
                    recipient_sha256,
                    encrypted_sha256,
                    encrypted_size,
                    str(input_path),
                    str(output_path),
                    sample_id,
                    run_id,
                    completed_at,
                ),
            )

    def list_encryptions(self, limit: int = 100) -> list[EncryptionRecord]:
        """Return the most recent successful encryptions."""
        rows = self.connection.execute(
            """
            SELECT source_sha256, recipient_sha256, encrypted_sha256,
                   encrypted_size, input_path, output_path, sample_id,
                   run_id, completed_at
            FROM encryptions
            ORDER BY completed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [EncryptionRecord(**dict(row)) for row in rows]

    def record_upload(
        self,
        *,
        sha256: str,
        endpoint: str,
        remote_path: str,
        local_path: Path,
        sample_id: str,
        run_id: str,
    ) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO inbox_uploads (
                    sha256, endpoint, remote_path, local_path, sample_id,
                    run_id, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (sha256, endpoint) DO UPDATE SET
                    remote_path = excluded.remote_path,
                    local_path = excluded.local_path,
                    sample_id = excluded.sample_id,
                    run_id = excluded.run_id,
                    completed_at = excluded.completed_at
                """,
                (
                    sha256,
                    endpoint,
                    remote_path,
                    str(local_path),
                    sample_id,
                    run_id,
                    completed_at,
                ),
            )

    def list_uploads(self, limit: int = 100) -> list[UploadRecord]:
        """Return the most recent successful uploads."""
        rows = self.connection.execute(
            """
            SELECT sha256, endpoint, remote_path, local_path, sample_id,
                   run_id, completed_at
            FROM inbox_uploads
            ORDER BY completed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [UploadRecord(**dict(row)) for row in rows]


def inbox_endpoint(host: str, port: int, username: str) -> str:
    """Return the stable Inbox identity used to scope upload records."""
    return f"{username}@{host.lower()}:{port}"
