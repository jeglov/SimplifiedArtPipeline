# Backlog: known issues deferred to later stages

Issues spotted during the build and deliberately postponed. Each one has the
stage where it should be fixed and why it matters. Move an item to **Done**
(with the commit) once it's resolved.

Stages: P1.3 validator · P1.4 fixes · P1.5 tests · P1.6 docs ·
P2 Unity import · P3 AI generation · P4 banners · P5 MCP agent + docs.

## Open

| # | Stage | Issue | Why it matters | Plan |
|---|---|---|---|---|
| 1 | P1.3 | `mayapy` cold start is about 21 s per process | One process per asset means 100 assets ≈ 35 min of pure startup | The CLI accepts many files per run and writes one report per file. In P5, consider a persistent Maya worker behind the MCP server |
| 2 | P1.3 | The Maya path `C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe` is hardcoded in README and commands | Breaks on any other machine or Maya version | Add a wrapper script that reads a `MAYAPY` env var and falls back to auto-detecting the newest Maya |
| 3 | P1.3 | `source.path` in the report is echoed exactly as given | Absolute paths leak machine layout, and the same asset gives different reports on different machines | Store a repo-relative path when the file is inside the repo. The hash stays the identity |
| 4 | P1.3 | For **FBX** inputs, `cmds.currentUnit()` reports Maya's preference, not the file's units | `scene.units` would pass while the FBX was authored in meters | Read the FBX header's `UnitScaleFactor` (or the importer's unit info) for FBX inputs. Use `currentUnit` only for `.ma`/`.mb` |
| 5 | P1.3 | `severity_overrides` check IDs in `mesh_budgets.yaml` are only format-checked | A typo like `topology.ngon` would silently never apply | Once the registry exists, fail at load time on unknown check IDs |
| 6 | P1.3 | The asset class comes from a CLI argument | An agent or an artist can pass the wrong class | P2/P5: infer it from the folder (`Assets/Art/Props/Small/`) or an asset manifest, and keep the CLI flag as an override |
| 7 | P1.3 | `OFFENDER_CAP = 50` is global | A fix-planning agent may need the full list | Add a `--max-offenders` CLI flag (0 = unlimited) |
| 8 | P1.4 | The report has no `fixes` section yet | Agents must see what was changed, not just the final state | Add `fixes[]` (id, applied, changed nodes) and bump `SCHEMA_VERSION` to 1.1 |
| 9 | P1.5 | The smoke test was run by hand and isn't in the suite | Nothing guards against a Maya-side import regression | Add Maya integration tests with a session-scoped `maya.standalone` fixture |
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
| 20 | P1.3 | Hierarchy rules are documented in the schema but not enforced: at most one root group named like the file, `_LODn` present on all meshes or none, contiguous LOD levels, SM/SK prefix matching the class `mesh_types` | Unity's LODGroup auto-setup silently breaks on mismatched suffixes | Implement in the `naming.convention` check (cross-node rules, beyond regex) |
| 21 | P1.3 | `max_materials` is in the budgets, but no check is planned for it | Materials per mesh = draw calls, the main mobile cost for small props | Add a `budget.materials` check (16th check) |
| 22 | P2 | Texture rules (POT, max size, alpha, suffix) only exist as data | Phase 1 validates meshes only | Texture validator in P2 (Python + Pillow, or in the Unity TexturePostprocessor) |
| 23 | P2 | Budgets are educated defaults, not measured on a device | A reviewer may ask "how did you pick 500 tris?" | Profile a test scene on a low-end Android device (Unity Profiler / Frame Debugger) and record the numbers in `docs/conventions.md` |

## Done

| # | Stage | Issue | Resolved in |
|---|---|---|---|
| - | P1.0 | Project was inside a git repo rooted at the user's home folder | `git init` in `ArtPipeline/` (initial commit) |
