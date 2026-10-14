# artpipe

A mini, agent-runnable art pipeline for live mobile games: assets are
validated, auto-fixed where safe, and imported into Unity with no UI and
machine-readable JSON at every step.

> Status: Phase 1 (conventions + headless Maya validator) in progress.

## Requirements
- Maya 2023+ (`mayapy`, Python 3.9). Code is kept 3.9-compatible.
- `mayapy -m pip install --user -r requirements-maya.txt`

## Run the tests
```powershell
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" -m pytest -q
```
