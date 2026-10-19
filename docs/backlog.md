# Backlog: known issues deferred to later stages

Issues spotted during the build and deliberately postponed. Each one has the
stage where it should be fixed and why it matters. Move an item to **Done**
(with the commit) once it's resolved.

Stages: P1.3 validator · P1.4 fixes · P1.5 tests · P1.6 docs ·
P2 Unity import · P3 AI generation · P4 banners · P5 MCP agent + docs.

## Open

| # | Stage | Issue | Why it matters | Plan |
|---|---|---|---|---|
| 1 | P5 | `mayapy` cold start is about 21 s per process | One process per asset means 100 assets ≈ 35 min of pure startup | The CLI accepts many files per run and writes one report per file. In P5, consider a persistent Maya worker behind the MCP server The CLI already validates many files per run; what's left is the persistent worker. |
| 4 | P3 | For **FBX** inputs, the file's own unit and up-axis are lost: Maya converts FBX to the scene's preferences on open, so `scene.units` reports "not verifiable". For `.ma` the unit is stored, but up-axis isn't stored at all (it's a Maya preference) | A Z-up or metre-unit FBX isn't flagged by `scene.units` (`transform.size` still catches a 100x scale error) | Parse the FBX header `GlobalSettings` (`UnitScaleFactor`, `UpAxis`) before opening. Possibly a P3 need too: AI image-to-3D outputs (GLB/FBX) come in metres and often Z-up |
| 12 | P2 | Skinned meshes have no checks: bone count, influences per vertex (mobile: 2–4), bind pose | `character` and `crowd_unit` classes exist but skinning isn't validated | Add a `skin.*` check family (Maya side) plus the Unity import setting `skinWeights` |
| 13 | P2 | Texel density isn't checked | Mixed densities look inconsistent and waste memory | Add a `uv.texel_density` check with px/m targets per class in `mesh_budgets.yaml` |
| 16 | P5 | No CI. Maya can't run on a hosted CI runner (license) | Regressions in core logic go unnoticed | GitHub Actions runs the pure-Python `validate.core` tests on stock Python. Maya tests run locally or on a licensed self-hosted runner |
| 17 | P5 | Dependencies are installed with `pip --user` into `%APPDATA%\Python\Python39`, which is shared by every Python 3.9 | Can clash with other tools, and isn't reproducible on a farm | Vendor them into `third_party/` with `pip install --target`, and the wrapper script puts that folder on `PYTHONPATH` |
| 18 | P5 | Only tested on Maya 2023 (Python 3.9). Maya 2025+ uses Python 3.11 | Studios run newer Maya | Test matrix on 2023 and 2025+ if available. Code stays 3.9-compatible |
| 19 | P5 | The package is found through pytest `pythonpath = ["."]`, not installed | Fine for a repo tool, odd for reuse | Revisit if artpipe needs to be pip-installable |
| 23 | P2 | Budgets are educated defaults, not measured on a device | A reviewer may ask "how did you pick 500 tris?" | Profile a test scene on a low-end Android device (Unity Profiler / Frame Debugger) and record the numbers in `docs/conventions.md` |
| 25 | P1.3 | `uv.unit_square` tolerance is a fixed `1e-4` | Maya's own default cylinder overshoots to u = 1.0019, invisible at the texture sizes we ship | Consider a per-class tolerance of half a texel at `texture_max_size` (prop_small: 1/512) and keep the fixed epsilon as the floor |
| 26 | P2 | Only UV0 is checked for overlap. A hand-authored UV1 (lightmap) must NOT overlap | Overlapping lightmap UVs bake light from one surface onto another | If UV1 exists, check it with overlap never allowed. Otherwise rely on Unity's "Generate Lightmap UVs" |
| 29 | P1.5 | No check for a shape's `opposite` flag (set when Maya freezes a negative scale) | FBX doesn't carry the flag: the mesh imports into Unity inside out while it looks fine in Maya | Add a `topology.inverted` check (opposite flag set, or normals pointing inward on a closed mesh). The freeze fix already refuses negative scale |
| 30 | P5 | A Unity batchmode boot costs ~15-20 s warm, and ~7 min on a cold Library (first URP import). `artpipe test` boots Unity 3 times (~60 s) | Same problem as #1: one process per call doesn't scale to hundreds of assets | Import many assets per boot (step 7 already takes a list). In P5, consider a persistent Editor worker, and a cached `Library/` on build machines |
| 31 | P5 | Unity tests need an activated licence. Unity Personal can't be activated headless on a hosted CI runner | Same gap as #16, for the C# side | Run the `unity` marker locally or on a self-hosted runner. A Pro or build-server licence (or GameCI with a licence secret) for real CI |
| 32 | P2 | `env.render_pipeline` checks only `GraphicsSettings.defaultRenderPipeline`. A quality level can override it | `artpipe unity setup` clears every override, but a hand edit could bring one back unnoticed | Also check `QualitySettings` overrides for every level, or use `currentRenderPipeline` per level |
| 34 | P5 | Unity finds the rules at `../conventions/` (this repo's layout) or `ARTPIPE_CONVENTIONS_DIR` | 40 game projects won't sit next to this repo | Ship the lock with a versioned package or fetch it from a conventions service. The hash check already makes a stale copy fail loudly |
| 35 | P3 | The FBX canonicalizer is probed on Maya 2023 / FBX SDK 2020.3.1 output only | FBX from Blender, other SDK versions or AI image-to-3D tools may lay out headers differently | Every unknown shape fails closed today (tool error, nothing shipped). In P3, add samples from each producer to the tests, or route them through Maya (import, validate, export) |
| 36 | P2 | ASCII FBX is bigger and slower to parse than binary | Measured: 12 KB for a 12-tri cube, 135 KB for the 940-tri LOD fixture (~140 bytes per triangle), so a 5k-tri character is ~0.7 MB. Import time is not measured yet | Measure file size and Unity import time per class in step 5. Binary FBX would need a different determinism strategy (hash a canonical ASCII twin) |
| 37 | P2 | The conventions custom dependency is registered on domain reload and by the CLI, not when a YAML file changes while the Editor is open | An artist editing rules in an open Editor sees old verdicts until the next script reload | The CLI always registers before importing (authoritative path). Optionally a FileSystemWatcher on conventions/ in P5 |
| 38 | P5 | Sidecar reports are not signed: anyone can write a "pass" report with the right sha256 | The gate proves consistency, not provenance | Sign reports (HMAC with a pipeline secret, or Sigstore-style signatures) when agents from several teams write into one project |
| 39 | P2 | An FBX that arrives in the same refresh as its sidecar is imported twice (the sidecar is a declared dependency, so it triggers a second import) | Doubles import time for batches | The batchmode CLI imports sidecars first, then the FBX files (step 7) |
| 40 | P3 | Texture checks run in Unity only; a texture isn't validated before it reaches the project | An AI-generated texture with the wrong size or alpha is only caught at import | Run the same rules in Python (a pure PNG/TGA header reader, no Pillow) at generation time in P3 |

## Done

| # | Stage | Issue | Resolved in |
|---|---|---|---|
| 33 | P2 | `-nographics` not proven for every import path | Probed and tested: with no GPU device, the Android target still gets real ASTC_6x6 / ASTC_4x4 (P2 step 6) |
| 22 | P2 | Texture rules (POT, size, alpha, name) existed only as data | `TextureRulesUnity.SourceChecks`: texture.name, source_format, power_of_two, min_size, square, source_size (warning), alpha; plus texture.platform_format on the imported result (P2 step 6) |
| 14 | P2 | `import_hints` had no consumer | `TexturePostprocessor` applies type, sRGB/linear, alpha source, opacity vs data alpha, mipmaps, class max size, and Android/iOS ASTC overrides from the lock (P2 step 6) |
| 11 | P2 | GPU cost is the vertex count after seam and hard-edge splits, not Maya's count | `ModelChecks` measures after import: `unity.vertex_budget` (`verts_max` per class), `unity.lod_vertex_ratio`, `unity.submeshes`, `unity.lod_group`. It caught SM_LodsGood: LOD1 keeps 87% of LOD0's vertices (P2 step 5) |
| 6 | P5 | The asset class came only from a CLI argument | In Unity the class is the folder (`Assets/Art/<class>/`), and `gate.asset_class` refuses a mismatch with the report. The Maya CLI keeps `--class` (P2 step 5) |
| 15 | P2 | No JSON Schema for the report | `conventions/report.schema.json`, enforced on every report the Maya suite writes (jsonschema 4.17.3, pure Python) and read strictly by the Unity gate (P2 step 4) |
| 27 | P2 | `--fix` couldn't save fixed FBX inputs; Unity needs FBX in and FBX out | `--export DIR`: canonical ASCII FBX with explicit, read-back-verified settings (`conventions/fbx_export.yaml`), re-validated, with its own sha256-linked report. FBX inputs fix into a `.ma` working file (P2 step 3) |
| 28 | P2 | A fixed `.mb` got a new sha256 on every run | Fixed outputs are always `.ma` (volatile lines stripped), and exported FBX is canonicalized (P2 step 3) |
| 10 | P2 | Unity C# can't read YAML without an extra package | `artpipe conventions --dump` writes a resolved, hash-linked `conventions.lock.json`. Strict typed C# loader (Newtonsoft), stale lock = exit 2 in Unity and a failing test (P2 step 2) |
| 9 | P1.5 | The smoke test was run by hand and isn't in the suite | `tests/integration/`: session-scoped Maya, 224 tests incl. every fixture before/after `--fix`, determinism, read-only, FBX round trip (step 5) |
| 8 | P1.4 | The report has no `fixes` section yet | `fixes[]`, `fixed_from`, `summary.fixes_applied`; `SCHEMA_VERSION` 1.1 (step 4.1) |
| 2 | P1.3 | The Maya path `C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe` is hardcoded in README and commands | `artpipe.cmd` wrapper: `MAYAPY` env or newest installed Maya (step 3.7) |
| 3 | P1.3 | `source.path` in the report is echoed exactly as given | `validate.core.paths.report_path`: repo-relative POSIX paths (step 3.7) |
| 7 | P1.3 | `OFFENDER_CAP = 50` is global | `--max-offenders N` (0 = all). The cap is applied on output, results keep every offender (step 3.7) |
| 24 | P1.3 | `budget.materials` counts materials per mesh, but faces with NO material on a partly assigned mesh aren't detected | `budget.materials` reports uncovered faces via `meshes.material_faces` (step 3.7) |
| 5 | P1.3 | `severity_overrides` check IDs in `mesh_budgets.yaml` are only format-checked | `registry.unknown_override_ids()`: the CLI exits 2 on an unknown check id (step 3.6) |
| 20 | P1.3 | Hierarchy rules are documented in the schema but not enforced: at most one root group named like the file, `_LODn` present on all meshes or none, contiguous LOD levels, SM/SK prefix matching the class `mesh_types` | Hierarchy rules in the `naming.convention` check (step 3.6) |
| 21 | P1.3 | No check for `max_materials` | `budget.materials` check (step 3.3) |
| - | P1.0 | Project was inside a git repo rooted at the user's home folder | `git init` in `ArtPipeline/` (initial commit) |
