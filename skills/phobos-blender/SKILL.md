---
name: phobos-blender
description: "Generate editable Blender scenes and previews from bounded JSON scene descriptions."
---

# Phobos Blender

Requires Blender on the host running the CLI. Heavy rendering can run on a separate machine.
Create a project-local JSON file with 1–32 `objects`, each with `shape` (`cube` or `sphere`),
optional `name`, `location` (3 numbers), `scale` (3 numbers), and `color` (4 numbers, 0–1).

```json
{"objects":[{"shape":"sphere","name":"Phobos","color":[0.7,0.2,0.1,1]}]}
```

```sh
phobos creative blender --project personal --source scene.json --output renders/phobos.blend
```

Outputs an editable `.blend` and a 640px PNG. The fixed generator sets camera and lighting.
Do not claim unrestricted Blender scripting or completed rendering without output evidence.
