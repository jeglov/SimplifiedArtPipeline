"""artpipe validation package.

Layout rule (enforced by tests/test_core.py):
    validate.core  -> pure Python, must NEVER import maya. Runs under any
                      Python 3.9+, so agents/CI can parse conventions and
                      reports without paying Maya's ~20 s startup.
    validate.maya  -> everything that needs maya.cmds / OpenMaya. Runs under
                      mayapy only.
"""
