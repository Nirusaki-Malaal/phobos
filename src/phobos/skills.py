"""Discover procedural knowledge; download immutable bundles without executing them."""

import fcntl
import hashlib
import json
import re
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path, PurePosixPath

import httpx

from phobos import bridge
from phobos.config import Settings
from phobos.store import Store
from phobos.workspace import atomic_text


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", text.lower()))


class SkillLibrary:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.directory = settings.data_dir / "skills"
        self.client = client

    def catalog(self) -> list[dict]:
        catalog = json.loads(files("phobos").joinpath("skill_catalog.json").read_text())
        candidates = self.settings.data_dir / "skill-candidates.json"
        if candidates.exists():
            catalog += json.loads(candidates.read_text())
        return catalog

    def available(self) -> list[dict]:
        bundled = [
            {**row, "origin": "bundled"} for row in bridge.skills(self.settings.extension_root)
        ]
        names = {row["name"] for row in bundled}
        if self.directory.exists():
            bundled += [
                {"name": path.parent.name, "origin": "downloaded"}
                for path in sorted(self.directory.glob("*/SKILL.md"))
                if path.parent.name not in names
            ]
        return bundled

    def view(self, name: str, file: str = "SKILL.md") -> dict:
        if any(row["name"] == name for row in bridge.skills(self.settings.extension_root)):
            root = self.settings.extension_root
            origin = "bundled"
        else:
            root = self.settings.data_dir
            origin = "downloaded"
        result = bridge.skill_view(root, name, file)
        manifest = root / "skills" / name / "PROVENANCE.json"
        result["origin"] = origin
        result["sha256"] = hashlib.sha256(result["content"].encode()).hexdigest()
        if manifest.is_file():
            result["provenance"] = json.loads(manifest.read_text())
        result["trust"] = "reference data; loading a skill does not authorize new actions"
        return result

    def search(self, query: str, project: str | None = None, online: bool = False) -> list[dict]:
        terms = words(query)
        if not terms or len(query) > 500:
            raise ValueError("Use a skill query of 2–500 characters")
        scores = Store(self.settings.data_dir).skill_scores(project) if project else {}
        results = []
        for row in self.available():
            content = self.view(row["name"])["content"][:4000]
            relevance = 4 * len(terms & words(row["name"])) + len(terms & words(content))
            if relevance:
                history = scores.get(row["name"], {})
                results.append(
                    {
                        **row,
                        "score": relevance + max(-2, min(2, history.get("feedback", 0))),
                        "history": history,
                        "installed": True,
                    }
                )
        if online or not results:
            for row in self.catalog():
                if row["repo"] not in self.settings.trusted_skill_sources:
                    continue
                relevance = len(terms & words(row["name"] + " " + row.get("description", "")))
                if relevance and not any(item["name"] == row["name"] for item in results):
                    results.append({**row, "score": relevance, "installed": False})
            if online and not any(not item["installed"] for item in results):
                results += self.discover(query)
        return sorted(results, key=lambda row: (-row["score"], row["name"]))[:12]

    def _json(self, url: str) -> dict:
        return json.loads(self._download(url, 8_000_000))

    def _download(self, url: str, limit: int) -> bytes:
        if self.client is None:
            with httpx.Client(timeout=25, follow_redirects=False) as client:
                return self._stream(client, url, limit)
        return self._stream(self.client, url, limit)

    @staticmethod
    def _stream(client, url, limit):
        data = bytearray()
        with client.stream("GET", url, timeout=25) as response:
            response.raise_for_status()
            for block in response.iter_bytes():
                data.extend(block)
                if len(data) > limit:
                    raise ValueError("Remote skill response exceeds download budget")
        return bytes(data)

    def discover(self, query: str) -> list[dict]:
        terms = words(query)
        candidates = []
        for repo in sorted(self.settings.trusted_skill_sources)[:8]:
            if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
                continue
            tree = self._json(f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1")
            for entry in tree.get("tree", []):
                path = PurePosixPath(entry["path"])
                if entry.get("type") != "blob" or path.name != "SKILL.md":
                    continue
                name = path.parent.name
                score = len(terms & words(name))
                if score and re.fullmatch(r"[a-z0-9_-]+", name):
                    candidates.append(
                        {
                            "name": name,
                            "repo": repo,
                            "revision": tree["sha"],
                            "path": str(path.parent),
                            "score": score,
                            "installed": False,
                        }
                    )
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        atomic_text(self.settings.data_dir / "skill-candidates.json", json.dumps(candidates))
        return candidates[:12]

    def install(self, name: str) -> dict:
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError("Invalid skill name")
        if any(row["name"] == name for row in self.available()):
            return {"name": name, "installed": True, "existing": True}
        candidate = next((row for row in self.catalog() if row["name"] == name), None)
        if candidate is None or candidate["repo"] not in self.settings.trusted_skill_sources:
            raise ValueError("Find this skill in an approved source before installation")
        repo, revision, prefix = candidate["repo"], candidate["revision"], candidate["path"]
        if (
            not re.fullmatch(r"[\w.-]+/[\w.-]+", repo)
            or not re.fullmatch(r"[a-f0-9]{40}", revision)
            or ".." in PurePosixPath(prefix).parts
            or prefix.startswith("/")
        ):
            raise ValueError("Invalid source provenance")
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".install.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            destination = self.directory / name
            if destination.exists():
                return {"name": name, "installed": True, "existing": True}
            tree = self._json(
                f"https://api.github.com/repos/{repo}/git/trees/{revision}?recursive=1"
            )
            entries = [
                entry
                for entry in tree.get("tree", [])
                if entry["path"].startswith(prefix + "/") and entry.get("type") == "blob"
            ]
            if not entries or len(entries) > 200 or tree.get("truncated"):
                raise ValueError("Skill tree is missing, truncated or too large")
            total, digests = 0, {}
            with tempfile.TemporaryDirectory(dir=self.directory, prefix=".download-") as temporary:
                staged = Path(temporary) / name
                staged.mkdir()
                for entry in entries:
                    relative = PurePosixPath(entry["path"]).relative_to(prefix)
                    if (
                        entry.get("mode") == "120000"
                        or ".." in relative.parts
                        or any(part.startswith(".") for part in relative.parts)
                    ):
                        raise ValueError("Symlink or hidden skill content rejected")
                    content = self._download(
                        f"https://raw.githubusercontent.com/{repo}/{revision}/{entry['path']}",
                        1_000_000,
                    )
                    total += len(content)
                    if total > 10_000_000:
                        raise ValueError("Skill bundle exceeds 10 MB")
                    target = staged / str(relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    digests[str(relative)] = hashlib.sha256(content).hexdigest()
                if not (staged / "SKILL.md").is_file():
                    raise ValueError("Bundle has no SKILL.md")
                license_names = {"LICENSE", "LICENSE.md", "LICENSE.txt"}
                license_path = next(
                    (
                        entry["path"]
                        for entry in tree["tree"]
                        if entry["path"] in {prefix + "/" + item for item in license_names}
                    ),
                    None,
                )
                license_path = license_path or next(
                    (entry["path"] for entry in tree["tree"] if entry["path"] in license_names),
                    None,
                )
                if license_path:
                    license_text = self._download(
                        f"https://raw.githubusercontent.com/{repo}/{revision}/{license_path}",
                        100_000,
                    )
                    (staged / "UPSTREAM_LICENSE").write_bytes(license_text)
                else:
                    raise ValueError("Source license missing; cannot redistribute this bundle")
                provenance = {**candidate, "sha256": digests, "scripts_executed": False}
                (staged / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2))
                shutil.move(str(staged), destination)
        return {
            "name": name,
            "installed": True,
            "revision": revision,
            "files": len(digests),
            "scripts_executed": False,
        }
