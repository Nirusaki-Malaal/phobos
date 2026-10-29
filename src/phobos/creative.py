"""Local creative jobs. Fixed commands, project paths, and no downloaded inputs."""

import importlib.util
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from phobos.workspace import confined


def capabilities() -> dict:
    return {
        "clip": bool(shutil.which("ffmpeg")),
        "audio": bool(shutil.which("ffmpeg")),
        "blender": bool(shutil.which("blender")),
        "transcribe": importlib.util.find_spec("faster_whisper") is not None,
        "design": "OpenDesign profile",
        "motion": bool(shutil.which("om")),
    }


def _run(binary: str, arguments: list[str], timeout: int = 300) -> None:
    executable = shutil.which(binary)
    if not executable:
        raise ValueError(f"Install {binary} on this worker before running this job")
    try:
        result = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError(f"{binary} exceeded its {timeout}-second budget") from None
    if result.returncode:
        raise ValueError(f"{binary} failed: {result.stderr[-1500:]}")


def media(
    root: Path,
    source: str,
    output: str,
    *,
    start: float = 0,
    duration: float = 30,
    audio_only: bool = False,
) -> dict:
    if not math.isfinite(start) or not math.isfinite(duration):
        raise ValueError("Time values must be finite")
    if start < 0 or not 0 < duration <= 600:
        raise ValueError("Start must be nonnegative; duration must be 0–600 seconds")
    source_path, output_path = confined(root, source), confined(root, output)
    if not source_path.is_file():
        raise ValueError("Input must be an existing project file")
    expected = ".wav" if audio_only else ".mp4"
    if output_path.suffix.lower() != expected:
        raise ValueError(f"Output must use {expected}")
    if output_path.exists():
        raise ValueError("Output already exists; choose a new filename")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Only local file input is allowed; playlists cannot fetch network resources.
    args = [
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-protocol_whitelist",
        "file,pipe",
        "-ss",
        str(start),
        "-i",
        str(source_path),
        "-t",
        str(duration),
    ]
    args += (
        ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
        if audio_only
        else [
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
        ]
    )
    try:
        _run("ffmpeg", [*args, str(output_path)])
    except ValueError:
        output_path.unlink(missing_ok=True)
        raise
    return {
        "artifact": output,
        "bytes": output_path.stat().st_size,
        "start": start,
        "requested_duration": duration,
    }


BLENDER_SCRIPT = """import bpy, json, sys
from mathutils import Vector
config = json.loads(sys.argv[sys.argv.index("--") + 1])
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
for item in config["objects"]:
    if item["shape"] == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(location=item["location"])
    else:
        bpy.ops.mesh.primitive_cube_add(location=item["location"])
    obj = bpy.context.object
    obj.name = item["name"]
    obj.scale = item["scale"]
    mat = bpy.data.materials.new(item["name"] + " material")
    mat.diffuse_color = item["color"]
    obj.data.materials.append(mat)
bpy.ops.object.light_add(type="AREA", location=(3, -4, 6))
bpy.context.object.data.energy = 700
bpy.context.object.data.shape = "DISK"
bpy.context.object.data.size = 5
bpy.ops.object.camera_add(location=(6, -8, 5))
camera = bpy.context.object
camera.rotation_euler = (Vector((0, 0, 0)) - camera.location).to_track_quat("-Z", "Y").to_euler()
scene = bpy.context.scene
scene.camera = camera
scene.render.engine = "CYCLES"
scene.cycles.samples = 16
scene.render.resolution_x = 640
scene.render.resolution_y = 640
scene.render.resolution_percentage = 100
scene.render.filepath = config["preview"]
bpy.ops.wm.save_as_mainfile(filepath=config["blend"])
bpy.ops.render.render(write_still=True)
"""


def scene(root: Path, specification: str, output: str) -> dict:
    spec_path = confined(root, specification)
    if spec_path.stat().st_size > 32000:
        raise ValueError("Scene specification exceeds 32000 bytes")
    spec = json.loads(spec_path.read_text())
    objects = spec.get("objects")
    if not isinstance(objects, list) or not 1 <= len(objects) <= 32:
        raise ValueError("Scene needs 1–32 objects")
    normalized = []
    for index, obj in enumerate(objects):
        if obj.get("shape") not in {"cube", "sphere"}:
            raise ValueError("Supported shapes: cube, sphere")
        item = {"name": str(obj.get("name", f"object-{index}"))[:80], "shape": obj["shape"]}
        for key, default, size in (
            ("location", [0, 0, 0], 3),
            ("scale", [1, 1, 1], 3),
            ("color", [0.3, 0.5, 0.9, 1], 4),
        ):
            value = obj.get(key, default)
            if (
                not isinstance(value, list)
                or len(value) != size
                or any(
                    type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 100
                    for n in value
                )
            ):
                raise ValueError(f"Invalid {key} vector")
            if key == "color" and any(not 0 <= n <= 1 for n in value):
                raise ValueError("Colors must be between 0 and 1")
            item[key] = value
        normalized.append(item)
    blend = confined(root, output)
    preview = blend.with_suffix(".png")
    if blend.suffix != ".blend" or blend.exists() or preview.exists():
        raise ValueError("Choose an unused .blend output and preview filename")
    blend.parent.mkdir(parents=True, exist_ok=True)
    config = {"objects": normalized, "blend": str(blend), "preview": str(preview)}
    with tempfile.TemporaryDirectory(prefix="phobos-scene-") as temporary:
        script = Path(temporary) / "scene.py"
        script.write_text(BLENDER_SCRIPT)
        _run(
            "blender",
            [
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--python-exit-code",
                "1",
                "--python",
                str(script),
                "--",
                json.dumps(config),
            ],
            timeout=600,
        )
    if not blend.is_file() or not preview.is_file():
        raise ValueError("Blender did not create both expected artifacts")
    return {"artifact": output, "preview": str(preview.relative_to(root.resolve()))}


def transcribe(root: Path, source: str, output: str, model_dir: str) -> dict:
    """Use only a provisioned local model; transcription never downloads weights."""
    source_path, output_path = confined(root, source), confined(root, output)
    if not source_path.is_file() or output_path.suffix not in {".json", ".txt"}:
        raise ValueError("Use an existing recording and a .json or .txt output")
    if output_path.exists():
        raise ValueError("Output already exists")
    model = Path(model_dir).resolve()
    if not model.is_dir() or not (model / "model.bin").is_file():
        raise ValueError("Provision a local faster-whisper model directory with model.bin")
    # A subprocess bounds CPU/model work and keeps the web process lightweight.
    script = """import json, sys
from faster_whisper import WhisperModel
model = WhisperModel(sys.argv[1], device="cpu", compute_type="int8", local_files_only=True)
segments, info = model.transcribe(sys.argv[2], beam_size=1, vad_filter=True)
result = {"language": info.language, "segments": []}
for item in segments:
    result["segments"].append({"start": item.start, "end": item.end, "text": item.text})
    if len(result["segments"]) > 10000:
        raise ValueError("Recording exceeds segment budget")
print(json.dumps(result))
"""
    if importlib.util.find_spec("faster_whisper") is None:
        raise ValueError("Install the transcription extra: uv sync --extra transcription")
    import sys

    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(model), str(source_path)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("Transcription exceeded its 600-second budget") from None
    if result.returncode:
        raise ValueError("Local transcription failed; verify the model and recording")
    data = json.loads(result.stdout)
    from phobos.workspace import atomic_text

    content = (
        json.dumps(data, indent=2)
        if output_path.suffix == ".json"
        else "\n".join(row["text"].strip() for row in data["segments"])
    )
    atomic_text(output_path, content)
    return {"artifact": output, "language": data["language"], "segments": len(data["segments"])}


def motion(root: Path, directory: str, render: bool = False) -> dict:
    path = confined(root, directory)
    if render:
        if not (path / "index.html").is_file():
            raise ValueError("Motion project needs an editable index.html")
        _run("om", ["render", str(path), "--silent", "--workers", "1"], timeout=600)
        artifacts = sorted((path / "out").glob("*.mp4"))
        if not artifacts:
            raise ValueError("Motion renderer did not produce an MP4")
        return {"artifacts": [str(item.relative_to(root.resolve())) for item in artifacts]}
    if path.exists():
        raise ValueError("Choose a new motion project directory")
    _run("om", ["new", str(path)], timeout=60)
    if not (path / "index.html").is_file():
        raise ValueError("Motion runtime did not create editable source")
    return {"artifact": str((path / "index.html").relative_to(root.resolve()))}
