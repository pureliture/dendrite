from __future__ import annotations

import json
from pathlib import Path

from dendrite.cli import main
from dendrite.source_catalog import resolve_source_ref, scan_source_catalog


def test_source_catalog_scan_writes_public_metadata_and_private_index(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "design.md"
    source.write_text("token=secret\nSee /Users/example/private.txt\n", encoding="utf-8")
    public_out = tmp_path / "public.jsonl"
    private_index = tmp_path / "private" / "index.json"

    report = scan_source_catalog(
        root=root,
        root_id="project-root",
        device_id="device-a",
        public_out=public_out,
        private_index=private_index,
    )
    public_record = json.loads(public_out.read_text(encoding="utf-8").splitlines()[0])
    private_payload = json.loads(private_index.read_text(encoding="utf-8"))

    assert report["status"] == "ok"
    assert report["records_written"] == 1
    assert report["raw_paths_printed"] is False
    serialized_public = json.dumps(public_record, sort_keys=True)
    assert str(root) not in serialized_public
    assert "design.md" not in serialized_public
    assert "token=secret" not in serialized_public
    assert public_record["relative_path_hash"].startswith("sha256:")
    assert public_record["content_hash"].startswith("sha256:")
    private_entry = next(iter(private_payload["records"].values()))
    assert private_entry["absolute_path"] == str(source)


def test_source_catalog_resolve_requires_same_device_and_approval(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "design.md"
    source.write_text("token=secret\nSee /Users/example/private.txt\n", encoding="utf-8")
    public_out = tmp_path / "public.jsonl"
    private_index = tmp_path / "private" / "index.json"
    scan_source_catalog(
        root=root,
        root_id="project-root",
        device_id="device-a",
        public_out=public_out,
        private_index=private_index,
    )
    source_ref_id = json.loads(public_out.read_text(encoding="utf-8").splitlines()[0])["source_ref_id"]

    foreign = resolve_source_ref(
        private_index=private_index,
        source_ref_id=source_ref_id,
        requesting_device_id="device-b",
    )
    no_approval = resolve_source_ref(
        private_index=private_index,
        source_ref_id=source_ref_id,
        requesting_device_id="device-a",
    )
    resolved = resolve_source_ref(
        private_index=private_index,
        source_ref_id=source_ref_id,
        requesting_device_id="device-a",
        approval_ref="approval:test",
    )

    assert foreign["resolution_state"] == "same_device_required"
    assert no_approval["resolution_state"] == "approval_required"
    assert no_approval["content"] == ""
    assert resolved["resolution_state"] == "resolved"
    assert "token=secret" not in resolved["content"]
    assert "/Users/example" not in resolved["content"]
    assert "[redacted_path]" in resolved["content"]


def test_source_catalog_rescan_marks_deleted_without_printing_path(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "design.md"
    source.write_text("content", encoding="utf-8")
    public_out = tmp_path / "public.jsonl"
    private_index = tmp_path / "private" / "index.json"
    scan_source_catalog(
        root=root,
        root_id="project-root",
        device_id="device-a",
        public_out=public_out,
        private_index=private_index,
    )
    source.unlink()

    report = scan_source_catalog(
        root=root,
        root_id="project-root",
        device_id="device-a",
        public_out=public_out,
        private_index=private_index,
    )
    records = [json.loads(line) for line in public_out.read_text(encoding="utf-8").splitlines()]

    assert report["deleted_records"] == 1
    assert records[0]["deleted_at"]
    assert str(root) not in json.dumps(records, sort_keys=True)


def test_source_catalog_cli_scan_and_resolve(tmp_path: Path, capsys):
    root = tmp_path / "project"
    root.mkdir()
    (root / "design.md").write_text("content", encoding="utf-8")
    public_out = tmp_path / "public.jsonl"
    private_index = tmp_path / "private" / "index.json"

    scan_rc = main(
        [
            "source-catalog",
            "scan",
            "--root",
            str(root),
            "--root-id",
            "project-root",
            "--device-id",
            "device-a",
            "--public-out",
            str(public_out),
            "--private-index",
            str(private_index),
        ]
    )
    scan_report = json.loads(capsys.readouterr().out)
    source_ref_id = json.loads(public_out.read_text(encoding="utf-8").splitlines()[0])["source_ref_id"]
    resolve_rc = main(
        [
            "source-catalog",
            "resolve",
            "--private-index",
            str(private_index),
            "--source-ref-id",
            source_ref_id,
            "--requesting-device-id",
            "device-a",
        ]
    )
    resolve_report = json.loads(capsys.readouterr().out)

    assert scan_rc == 0
    assert scan_report["raw_paths_printed"] is False
    assert resolve_rc == 0
    assert resolve_report["resolution_state"] == "approval_required"
