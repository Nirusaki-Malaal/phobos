---
name: phobos-stream-clipping
description: "Cut local video segments and extract transcription-ready audio with FFmpeg."
---

# Phobos stream clipping

Check `phobos creative capabilities` first. FFmpeg must be installed on the CLI host.
Use local recordings in the selected project. Do not download or publish content automatically.
Choose a start time and duration (maximum 600 seconds); output filenames must be unused.

```sh
phobos creative clip --project personal --source recording.mp4 --output clips/highlight.mp4 --start 45 --duration 30
phobos creative audio --project personal --source recording.mp4 --output audio/segment.wav --start 45 --duration 30
```

Audio extraction creates mono 16kHz WAV for an external transcription engine.
Transcription, automatic highlight scoring, captions and posting are not implemented here.
