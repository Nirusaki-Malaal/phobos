# Third-party provenance

The new application in `src/phobos` was authored for this project. The pai-stack services, plugins, scripts, docs and skills are ported from commit `c86899da37ce05667bf5b9d3c295bacbc1ff997b`.

The original MIT copyright and license remain in [licenses/pai-stack.LICENSE](licenses/pai-stack.LICENSE). All skill licenses, VENDORED files and supporting references are retained. Renaming the repository does not remove attribution for reused code.

Port adaptations include project-scoped Compose operations, optional-profile defaults, gateway credential wiring, a compressor test import fix, notebook execution-directory validation, ADR path resolution, and the Phobos native-tool bridge. Native public tool names remain compatible.

Dependencies are recorded in `uv.lock` and keep their upstream licenses. Python/Debian container components, Hermes, DSH, LiteLLM, Terrain, OpenDesign, Mnemosyne and OpenCode remain upstream software. New creative integrations are not represented as newly authored Phobos implementations.

New Phobos application code and SVG branding are MIT licensed under the root LICENSE. Third-party files retain their existing terms.


## Additional skill bundles

- `reverse-engineer-anything`: morluto/rea, revision `6fa48f201f932c6c12b540ee9799895a0a436d00`, MIT.
- `huashu-motion`: alchaincyf/huashu-motion, revision `52467f99c602b76ba86d511d37acdfe93fa1e16e`, MIT.
- `algorithmic-art` and `web-artifacts-builder`: anthropics/skills, revision `ca45fc496a3436251f191354091d557da31c15b5`, with their per-bundle license files retained.

Each added external bundle contains `PROVENANCE.json` with hashes and `UPSTREAM_LICENSE`. No installation scripts were executed during skill import. REA's analysis engines and MCP registration are separate from its instruction bundle. The attempted React Native source import was omitted because a redistributable license file was not found; `phobos-react-native` is original project guidance.

Graph memory uses the official `graphifyy` package, pinned to 0.9.97 in uv.lock. Graphify and its dependencies retain their upstream licenses. The optional huashu runtime is pinned to npm package 0.3.1 and retains its MIT and third-party notices.
