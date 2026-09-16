"""Explicit read-only intent routing; business decisions remain in existing tools."""

from dataclasses import dataclass

from fulfillment import FulfillmentPlan
from intent import ParsedIntent
from rag import KnowledgeRetrieval
from rules import BusinessIssue, DecisionResult, valid_identifier
import tools


@dataclass(frozen=True)
class ToolExecution:
    intent: str
    tool_name: str | None = None
    result: DecisionResult | list[BusinessIssue] | FulfillmentPlan | KnowledgeRetrieval | None = None
    error: str | None = None
    po_id: str | None = None


def execute_intent(parsed_intent: ParsedIntent) -> ToolExecution:
    """Validate required inputs and preserve the tool's returned object unchanged."""
    if not isinstance(parsed_intent, ParsedIntent) or not isinstance(parsed_intent.intent, str):
        return ToolExecution("UNKNOWN", error="Invalid parsed intent.")
    intent = parsed_intent.intent
    if intent in {"UNKNOWN", "GENERAL_CHAT"}:
        return ToolExecution(intent)
    if intent == "CHECK_PO":
        name, tool, args = "check_po", tools.check_po, (parsed_intent.po_id,)
    elif intent == "CHECK_STOCK":
        name, tool, args = "check_stock", tools.check_stock, (
            parsed_intent.part_no, parsed_intent.requested_qty,
        )
        if type(parsed_intent.requested_qty) is not int or parsed_intent.requested_qty <= 0:
            return ToolExecution(intent, error="requested_qty must be a positive integer.")
    elif intent == "CHECK_DEALER":
        name, tool, args = "check_dealer", tools.check_dealer, (parsed_intent.dealer_id,)
    elif intent == "CHECK_PART":
        name, tool, args = "check_part", tools.check_part, (parsed_intent.part_no,)
    elif intent == "CHECK_CLAIM":
        name, tool, args = "check_claim", tools.check_claim, (parsed_intent.claim_id,)
    elif intent == "PLAN_FULFILLMENT":
        name, tool, args = "plan_fulfillment", tools.plan_fulfillment, (parsed_intent.po_id,)
    elif intent == "SEARCH_KNOWLEDGE":
        name, tool, args = "search_knowledge", tools.search_knowledge, (parsed_intent.query, 3)
    elif intent == "SCAN_ANOMALIES":
        name, tool, args = "scan_anomalies", tools.scan_anomalies, ()
    else:
        return ToolExecution(intent, error="Unsupported intent.")
    if args and not valid_identifier(args[0]):
        return ToolExecution(intent, error="A non-empty string identifier is required.")
    try:
        result = tool(po_id=args[0]) if intent == "PLAN_FULFILLMENT" else tool(*args)
    except Exception:
        # Do not expose backend exception details or manufacture a business result.
        return ToolExecution(intent, name, error="Deterministic tool execution failed.")
    return ToolExecution(intent, name, result, po_id=(
        parsed_intent.po_id if intent == "PLAN_FULFILLMENT" else None
    ))
