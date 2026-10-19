# artpipe

A mini, agent-runnable art pipeline for live mobile games: assets are
validated, auto-fixed where safe, and imported into Unity with no UI and
machine-readable JSON at every step.

> Status: Phase 1 (conventions + headless Maya validator) in progress.

## Requirements
- Windows, Maya 2023+ (`mayapy`, Python 3.9). Code is kept 3.9-compatible.
- Install the two dependencies into Maya's Python once:
  ```powershell
  .\artpipe.cmd py -m pip install --user -r requirements-maya.txt
  ```

`artpipe.cmd` finds `mayapy` itself: `%MAYAPY%` if set, otherwise the newest
installed Maya. Set `MAYAPY` to pin a version.

## Usage
```powershell
.\artpipe.cmd fixtures                                              # build the test scenes into out\fixtures
.\artpipe.cmd validate out\fixtures\SM_Clean.ma --class prop_small  # -> out\SM_Clean.ma.report.json
.\artpipe.cmd test                                                  # run the test suite
```
Exit codes: `0` asset passed, `1` asset failed, `2` tool error.
