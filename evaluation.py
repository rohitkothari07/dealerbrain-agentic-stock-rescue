"""Golden routing and independent deterministic-truth evaluation; no prose scoring."""

from dataclasses import dataclass, fields

from fulfillment import FulfillmentPlan
from intent import ParsedIntent, parse_intent
from llm_client import LLMClient, get_llm_status
from models import EvidenceRef
from rag import KnowledgeRetrieval
from router import execute_intent
from rules import DecisionResult


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    query: str
    expected: ParsedIntent
    tool: str
    status: str | None = None
    facts: tuple[tuple[str, object], ...] = ()
    issues: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    actual_intent: str
    intent_correct: bool
    parameters_correct: bool
    deterministic_result_correct: bool
    evidence_correct: bool | None

    @property
    def passed(self):
        return (self.intent_correct and self.parameters_correct and self.deterministic_result_correct
                and self.evidence_correct is not False)


def _knowledge(case_id, query, key):
    return GoldenCase(case_id, query, ParsedIntent("SEARCH_KNOWLEDGE", query=query),
                      "search_knowledge", "MATCHED", evidence=(EvidenceRef("knowledge", key),))


GOLDEN_CASES = (
    GoldenCase("G01", "Can PO-2026-1026 be fulfilled?",
               ParsedIntent("PLAN_FULFILLMENT", po_id="PO-2026-1026"), "plan_fulfillment",
               "PARTIALLY_FULFILLABLE", (("requested_qty", 20), ("network_available_qty", 4),
                                       ("planned_fulfillment_qty", 4), ("unresolved_remaining_qty", 16))),
    GoldenCase("G02", "Process PO-2026-1106", ParsedIntent("CHECK_PO", po_id="PO-2026-1106"),
               "check_po", "BLOCKED", issues=("DEALER_INELIGIBLE",)),
    _knowledge("G03", "What is the procedure for a suspended dealer?", "KB-009"),
    GoldenCase("G04", "Check stock for P-10036, quantity 20",
               ParsedIntent("CHECK_STOCK", part_no="P-10036", requested_qty=20), "check_stock",
               "WARNING", (("requested_qty", 20), ("available_qty", 4), ("deficit_qty", 16))),
    GoldenCase("G05", "Check part P-10036", ParsedIntent("CHECK_PART", part_no="P-10036"),
               "check_part", facts=(("part_no", "P-10036"),)),
    GoldenCase("G06", "Check dealer D007", ParsedIntent("CHECK_DEALER", dealer_id="D007"),
               "check_dealer", facts=(("dealer_id", "D007"),)),
    GoldenCase("G07", "Check claim C001", ParsedIntent("CHECK_CLAIM", claim_id="C001"), "check_claim"),
    GoldenCase("G08", "Scan for anomalies", ParsedIntent("SCAN_ANOMALIES"), "scan_anomalies", "SCANNED"),
    _knowledge("G09", "What should we do with negative available stock?", "KB-007"),
    _knowledge("G10", "What does the guidance say about hazmat shipping?", "KB-017"),
)


def evaluate_case(case, llm_client):
    parsed = parse_intent(case.query, llm_client)
    parameters = all(getattr(parsed, f.name) == getattr(case.expected, f.name)
                     for f in fields(ParsedIntent) if f.name != "intent")
    # Truth is measured independently using golden parameters, even if routing fails.
    # The overall verdict still fails on any routing or parameter mismatch.
    execution = execute_intent(case.expected)
    result = execution.result
    facts, issues, evidence, status = {}, (), (), None
    if isinstance(result, FulfillmentPlan):
        facts = {"requested_qty": result.requested_qty,
                 "network_available_qty": result.network_available_qty,
                 "planned_fulfillment_qty": result.fulfilled_qty,
                 "unresolved_remaining_qty": result.remaining_qty}
        status, issues, evidence = result.status.value, result.issues, result.evidence
    elif isinstance(result, DecisionResult):
        facts, status, issues, evidence = result.facts, result.status.value, result.issues, result.evidence
    elif isinstance(result, KnowledgeRetrieval):
        status, evidence = result.status, tuple(m.evidence for m in result.matches)
    elif isinstance(result, list):
        status, issues = "SCANNED", result
        evidence = tuple(e for issue in result for e in issue.evidence)
    correct = (
        execution.error is None and execution.tool_name == case.tool and status is not None
        and (case.status is None or status == case.status)
        and all(key in facts and facts[key] == value for key, value in case.facts)
        and set(case.issues) <= {i.code for i in issues}
    )
    return EvaluationResult(case.case_id, parsed.intent, parsed.intent == case.expected.intent,
                            parameters, correct,
                            set(case.evidence) <= set(evidence) if case.evidence else None)


def evaluate_cases(cases, llm_client):
    return tuple(evaluate_case(case, llm_client) for case in cases)


def main():
    if get_llm_status().state != "Configured":
        print("LLM disabled or configuration unavailable; evaluation not started.")
        return 1
    results = evaluate_cases(GOLDEN_CASES, LLMClient())
    for r in results:
        print(f"{r.case_id} {'PASS' if r.passed else 'FAIL'} intent={r.actual_intent} "
              f"parameters={r.parameters_correct} deterministic={r.deterministic_result_correct}")
    for label, attribute in (("Routing", "intent_correct"), ("Parameters", "parameters_correct"),
                             ("Deterministic truth", "deterministic_result_correct"),
                             ("Evidence", "evidence_correct")):
        values = [getattr(r, attribute) for r in results if getattr(r, attribute) is not None]
        print(f"{label}: {sum(values)}/{len(values)}")
    passed = all(r.passed for r in results)
    print("Overall: " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
