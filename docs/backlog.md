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
| 6 | P5 | The asset class comes from a CLI argument | An agent or an artist can pass the wrong class | P2/P5: infer it from the folder (`Assets/Art/Props/Small/`) or an asset manifest, and keep the CLI flag as an override |
| 10 | P2 | Unity C# can't read YAML without an extra package (YamlDotNet) | The Phase 2 importer needs the same budgets as the validator | Add `python -m validate.core.conventions --dump` → `conventions.lock.json`, read in C# with Newtonsoft. YAML stays the human-authored source of truth |
| 11 | P2 | Tri count is measured in Maya, but the GPU cost is the **vertex** count after UV-seam and hard-edge splits | A 500-tri mesh with many seams can have 3× the vertices | Measure post-import vertex count in the Unity AssetPostprocessor and add it to the budget |
| 12 | P2 | Skinned meshes have no checks: bone count, influences per vertex (mobile: 2–4), bind pose | `character` and `crowd_unit` classes exist but skinning isn't validated | Add a `skin.*` check family (Maya side) plus the Unity import setting `skinWeights` |
| 13 | P2 | Texel density isn't checked | Mixed densities look inconsistent and waste memory | Add a `uv.texel_density` check with px/m targets per class in `mesh_budgets.yaml` |
| 14 | P2 | `texture_rules.yaml` → `import_hints` isn't used by anything yet | Data with no consumer drifts out of date | Consume it in the Unity texture presets/postprocessor |
| 15 | P5 | There's no JSON Schema for the **report** itself | Agents and the Unity gate can't validate reports independently | Add `conventions/report.schema.json` and a test that every report validates against it |
| 16 | P5 | No CI. Maya can't run on a hosted CI runner (license) | Regressions in core logic go unnoticed | GitHub Actions runs the pure-Python `validate.core` tests on stock Python. Maya tests run locally or on a licensed self-hosted runner |
| 17 | P5 | Dependencies are installed with `pip --user` into `%APPDATA%\Python\Python39`, which is shared by every Python 3.9 | Can clash with other tools, and isn't reproducible on a farm | Vendor them into `third_party/` with `pip install --target`, and the wrapper script puts that folder on `PYTHONPATH` |
| 18 | P5 | Only tested on Maya 2023 (Python 3.9). Maya 2025+ uses Python 3.11 | Studios run newer Maya | Test matrix on 2023 and 2025+ if available. Code stays 3.9-compatible |
| 19 | P5 | The package is found through pytest `pythonpath = ["."]`, not installed | Fine for a repo tool, odd for reuse | Revisit if artpipe needs to be pip-installable |
| 22 | P2 | Texture rules (POT, max size, alpha, suffix) only exist as data | Phase 1 validates meshes only | Texture validator in P2 (Python + Pillow, or in the Unity TexturePostprocessor) |
| 23 | P2 | Budgets are educated defaults, not measured on a device | A reviewer may ask "how did you pick 500 tris?" | Profile a test scene on a low-end Android device (Unity Profiler / Frame Debugger) and record the numbers in `docs/conventions.md` |
| 25 | P1.3 | `uv.unit_square` tolerance is a fixed `1e-4` | Maya's own default cylinder overshoots to u = 1.0019, invisible at the texture sizes we ship | Consider a per-class tolerance of half a texel at `texture_max_size` (prop_small: 1/512) and keep the fixed epsilon as the floor |
| 26 | P2 | Only UV0 is checked for overlap. A hand-authored UV1 (lightmap) must NOT overlap | Overlapping lightmap UVs bake light from one surface onto another | If UV1 exists, check it with overlap never allowed. Otherwise rely on Unity's "Generate Lightmap UVs" |
| 27 | P2 | `--fix` can only save fixed `.ma`/`.mb` files. A fixed FBX input is refused | Unity imports FBX, so the agent loop needs FBX in and FBX out | Add FBX export with explicit, versioned exporter settings (smoothing groups, tangents, no cameras/lights) in the Unity import stage |
| 28 | P2 | Volatile lines (save time, file and node UUIDs) are stripped only from `.ma`. A fixed `.mb` gets a new sha256 on every run | Breaks the byte-identical promise for binary scenes | Keep fixed outputs as `.ma` (or FBX, see #27), or hash a canonical export instead of the file bytes |
| 29 | P1.5 | No check for a shape's `opposite` flag (set when Maya freezes a negative scale) | FBX doesn't carry the flag: the mesh imports into Unity inside out while it looks fine in Maya | Add a `topology.inverted` check (opposite flag set, or normals pointing inward on a closed mesh). The freeze fix already refuses negative scale |

## Done

| # | Stage | Issue | Resolved in |
|---|---|---|---|
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
