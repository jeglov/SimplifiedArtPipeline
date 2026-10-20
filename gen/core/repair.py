"""The repair loop: what to do when a generated asset fails its checks.

A deterministic policy, not an agent's judgement (that is P5): the first
blocking failure in a stage's report, looked up in conventions/generation.yaml
`repair.actions`, decides the next step:

  retry   run the same stage again with the next seed (bounded by max_attempts)
  stop    hand over to a human: retrying can't help (a rule or tool problem)

A tool error (exit 2) always stops: a broken tool doesn't get better with
another seed, and GPU minutes are the cost. Seeds follow a fixed sequence
(base, base + step, ...), so "attempt 3" means the same request on every
machine, and every attempt is recorded with its report and failing checks.

Pure Python 3.9: the engine takes the stage as a function, so it is tested
without ComfyUI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

RETRY, STOP, ACCEPT = "retry", "stop", "accept"


@dataclass(frozen=True)
class Policy:
    max_attempts: int
    seed_step: int
    actions: Mapping[str, str]      # check id -> retry | stop
    default: str                    # for a check id not in `actions`

    def action_for(self, check_id: str) -> str:
        return self.actions.get(check_id, self.default)


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

    def to_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage, "attempt": self.attempt, "seed": self.seed, "exit_code": self.exit_code,
                "report": self.report, "failed": list(self.failed), "decision": self.decision,
                "reason": self.reason}


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


StageFn = Callable[[int], Tuple[int, Optional[Mapping[str, Any]], Optional[str]]]


def run(stage: str, base_seed: int, policy: Policy, fn: StageFn) -> Tuple[Attempt, List[Attempt]]:
    """Run `fn(seed) -> (exit code, report dict, report path)` until accepted or stopped.

    Returns (last attempt, all attempts). The caller goes on only if the last
    decision is accept.
    """
    attempts = []
    for n in range(1, policy.max_attempts + 1):
        seed = base_seed + (n - 1) * policy.seed_step
        code, report, path = fn(seed)
        failed = blocking_failures(report)
        decision, reason = decide(code, failed, n, policy)
        a = Attempt(stage, n, seed, code, path, failed, decision, reason)
        attempts.append(a)
        if decision != RETRY:
            return a, attempts
    return attempts[-1], attempts  # unreachable: decide() stops at max_attempts
