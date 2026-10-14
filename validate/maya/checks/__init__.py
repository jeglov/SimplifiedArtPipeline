"""Validation checks: one module per check, all with the same contract.

Every check module exposes exactly these names:

    ID: str             Stable dotted ID, e.g. "topology.ngons". This is the
                        public API an agent calls; never rename it casually.
    SEVERITY: Severity  Default severity. Conventions may override per asset
                        class (e.g. ngons -> warning for hero props).
    FIXABLE: bool       True only if validate/maya/fixes/ has a SAFE,
                        deterministic fix registered for this ID.
    DESCRIPTION: str    One sentence, used in docs and agent tool listings.

    def run(ctx: CheckContext) -> CheckResult

Rules for run():
  * Read-only. Never modify the scene, the selection, or the undo queue.
    (No cmds.select / polySelectConstraint: selection is hidden global
    state and makes results depend on what ran before.)
  * Deterministic. Same scene in -> same CheckResult out. CheckResult
    itself sorts offenders and rounds floats; don't rely on Maya's
    iteration order for anything else either.
  * No UI, no prints. Return data; the CLI decides what to write where.

Checks are listed explicitly (in order) in validate/maya/registry.py rather
than auto-discovered, so a stray file here can't silently become a tool.
"""
