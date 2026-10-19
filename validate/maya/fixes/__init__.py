"""Safe auto-fixes: one module per fixable check, all with the same contract.

A fix is only allowed here if it is deterministic and cannot destroy artist
intent (bake history, delete a duplicate face, freeze transforms...).
Problems that need judgement (holes, non-manifold, over budget, missing
UVs) are reported, never auto-"fixed".

Every fix module exposes exactly these names:

    ID: str             The CHECK id this fix addresses, e.g. "scene.history".
    DESCRIPTION: str    One sentence: what the fix changes.

    def blocker(ctx, result) -> Optional[str]
        Read-only. None if the fix is safe to apply to this failure,
        otherwise the reason it isn't ("mesh is skinned"). The registry calls
        it after every check, so `fixable` in the report is true only when
        the fix would actually run.

    def apply(ctx, result) -> FixResult
        Modify the scene. Called only when blocker() returned None, with the
        FAILED CheckResult (its offenders say exactly what to fix). Must
        leave no construction history behind.

The fix is registered next to its check in validate/maya/registry.py, and
the check's FIXABLE constant must be True (the registry enforces both).
"""
