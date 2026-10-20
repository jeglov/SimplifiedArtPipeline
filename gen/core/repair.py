"""The repair loop: what to do when a generated asset fails its checks.

A deterministic policy, not an agent's judgement (that is P5): the first
blocking failure in a stage's report, looked up in conventions/generation.yaml
`repair.actions`, decides the next step:

  retry   run the same stage again with the next seed (bounded by max_attempts)
  stop    hand over to a human: retrying can't help (a rule or tool problem)

A tool error (exit 2) always stops: a broken tool doesn't get better with
another seed, and GPU minutes are the cost. Seeds follow a fixed sequence
(base, base + step, ...), so "attempt 3" means the same request on every
machine, and every attempt is recorded with its report and failing checks
(conventions/gen_repair.schema.json).

Pure Python 3.9: the engine takes the stage as a function, so it is tested
without ComfyUI.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

RETRY, STOP, ACCEPT = "retry", "stop", "accept"
SCHEMA_VERSION = "1.0"
TOOL = "artpipe.gen.repair"


@dataclass(frozen=True)
class Policy:
    max_attempts: int
    seed_step: int
    actions: Mapping[str, str]      # check id -> retry | stop
    default: str                    # for a check id not in `actions`

    def action_for(self, check_id: str) -> str:
        return self.actions.get(check_id, self.default)


@dataclass(frozen=True)
class Outcome:
    """What one run of a stage returned."""
    exit_code: int                              # 0 usable / 1 checks failed / 2 tool error
    report: Optional[Mapping[str, Any]] = None  # the stage's report.schema.json dict
    report_path: Optional[str] = None
    run: Optional[str] = None                   # run folder, repo-relative
    seconds: Optional[float] = None             # ComfyUI execution time of that run
    reused: bool = False                        # an identical earlier run: no GPU time spent now
    message: str = ""


@dataclass
class Attempt:
    stage: str
    attempt: int
    seed: int
    exit_code: int
    report: Optional[str]
    failed: List[str] = field(default_factory=list)   # blocking check ids, report order
    decision: str = ""
    reason: str = ""
    run: Optional[str] = None
    seconds: Optional[float] = None
    reused: bool = False
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage, "attempt": self.attempt, "seed": self.seed, "exit_code": self.exit_code,
                "report": self.report, "failed": list(self.failed), "decision": self.decision,
                "reason": self.reason, "run": self.run, "seconds": self.seconds, "reused": self.reused,
                "message": self.message}


def blocking_failures(report: Optional[Mapping[str, Any]]) -> List[str]:
    """Error-severity failed check ids, in report order (a report.schema.json dict)."""
    if not report:
        return []
    return [r["id"] for r in report.get("results") or []
            if not r.get("passed") and r.get("severity") == "error"]


def decide(exit_code: int, failed: Sequence[str], attempt: int, policy: Policy) -> Tuple[str, str]:
    """(accept | retry | stop, why). `attempt` counts from 1."""
    if exit_code == 0:
        return ACCEPT, "all blocking checks pass"
    if exit_code == 2:
        return STOP, "tool error: a broken tool doesn't improve with another seed"
    if not failed:
        return STOP, "failed without a blocking check to act on"
    first = failed[0]
    action = policy.action_for(first)
    if action == STOP:
        return STOP, "%s: the policy hands this to a human" % first
    if attempt >= policy.max_attempts:
        return STOP, "%s again after %d attempts: hand over" % (first, attempt)
    return RETRY, "%s: retry with the next seed" % first


StageFn = Callable[[int], Outcome]


def run(stage: str, base_seed: int, policy: Policy, fn: StageFn) -> Tuple[Attempt, List[Attempt]]:
    """Run `fn(seed) -> Outcome` until accepted or stopped.

    Returns (last attempt, all attempts). The caller goes on only if the last
    decision is accept.
    """
    attempts = []
    for n in range(1, policy.max_attempts + 1):
        seed = base_seed + (n - 1) * policy.seed_step
        out = fn(seed)
        failed = blocking_failures(out.report)
        decision, reason = decide(out.exit_code, failed, n, policy)
        a = Attempt(stage, n, seed, out.exit_code, out.report_path, failed, decision, reason,
                    out.run, out.seconds, out.reused, out.message)
        attempts.append(a)
        if decision != RETRY:
            return a, attempts
    return attempts[-1], attempts  # unreachable: decide() stops at max_attempts


def policy(rules: Any) -> Policy:
    """From gen.core.rules.RepairRules (conventions/generation.yaml `repair`)."""
    return Policy(rules.max_attempts, rules.seed_step, dict(rules.actions), rules.default)


def exit_code(last: Attempt) -> int:
    """0 accepted / 2 the stage's tool broke / 1 handed over with a bad asset."""
    if last.decision == ACCEPT:
        return 0
    return 2 if last.exit_code == 2 else 1


def repair_id(stage: str, request: Mapping[str, Any]) -> str:
    """<stage>-<12 hex>: the same request (inputs + base seed) always maps to one record."""
    blob = json.dumps({"stage": stage, "request": request}, sort_keys=True, separators=(",", ":"))
    return "%s-%s" % (stage, hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12])


def record(stage: str, request: Mapping[str, Any], pol: Policy, last: Attempt,
           attempts: Sequence[Attempt]) -> Dict[str, Any]:
    """The repair record (conventions/gen_repair.schema.json).

    gpu_seconds counts only attempts that ran now: a reused run cost nothing.
    """
    spent = [a.seconds for a in attempts if a.seconds is not None and not a.reused]
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "repair_id": repair_id(stage, request),
        "stage": stage,
        "request": dict(request),
        "policy": {"max_attempts": pol.max_attempts, "seed_step": pol.seed_step, "default": pol.default,
                   "actions": dict(sorted(pol.actions.items()))},
        "decision": last.decision,
        "reason": last.reason,
        "exit_code": exit_code(last),
        "accepted_run": last.run if last.decision == ACCEPT else None,
        "attempts": [a.to_dict() for a in attempts],
        "summary": {"attempts": len(attempts), "reused": sum(1 for a in attempts if a.reused),
                    "gpu_seconds": round(sum(spent), 3)},
    }
