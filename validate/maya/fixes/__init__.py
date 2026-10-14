"""Safe auto-fixes, one module per fixable check ID (implemented in step 4).

A fix is only allowed here if it is deterministic and cannot destroy artist
intent (e.g. delete history, freeze transforms). Topology/UV problems that
need judgement (open borders, overlapping UVs, over-budget meshes) are
reported, never auto-"fixed".
"""
