"""Read frozen official Senate image fixtures for source-backed tests."""
import hashlib,json,os,subprocess
from pathlib import Path
from typing import Callable
from unison_snapshot.senate_observation_data import load_bundle
EVIDENCE_COMMIT = load_bundle()['evidence_commit']

def fixed_archive_pages(repo: Path, inventory: dict) -> Callable[[str, int], bytes]:
    """Read only pages named by the fixed inventory and evidence manifests."""
    if inventory.get("evidence_commit") != EVIDENCE_COMMIT:
        raise ValueError("Senate paper inventory evidence commit changed")
    paths: dict[tuple[str, int], tuple[str, str]] = {}
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1")

    def git_bytes(path: str) -> bytes:
        proc = subprocess.run(["git", "-C", str(repo), "show",
                               f"{EVIDENCE_COMMIT}:{path}"],
                              capture_output=True, env=env, check=False)
        if proc.returncode:
            raise ValueError(f"fixed Senate paper archive unavailable: {path}")
        return proc.stdout

    for report in inventory["reports"]:
        document_id = report["document_id"]
        manifest = json.loads(git_bytes(
            f"senate_efd/paper_pages/{document_id}/manifest.json"))
        if manifest["manifest_sha256"] != report["manifest_sha256"]:
            raise ValueError("Senate paper manifest mismatch")
        for listed, archived in zip(report["pages"], manifest["pages"], strict=True):
            if listed["page_sha256"] != archived["sha256"]:
                raise ValueError("Senate paper inventory and manifest disagree")
            paths[(document_id, listed["page_number"])] = (
                archived["archive_path"], archived["sha256"])

    def read(document_id: str, page_number: int) -> bytes:
        path, expected = paths[(document_id, page_number)]
        raw = git_bytes(path)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Senate paper archived image SHA mismatch")
        return raw

    return read
