"""Safe deterministic tool endpoints; input validation is shared with the rules engine."""

import logging

from rules import RulesEngine
from fulfillment import build_fulfillment_plan, build_fulfillment_plan_for_po

logger = logging.getLogger(__name__)

__all__ = [
    "check_po",
    "check_stock",
    "check_dealer",
    "check_part",
    "check_claim",
    "check_warranty",
    "scan_anomalies",
    "plan_fulfillment",
]


def _invoke(tool_name, method, *args):
    # Rule entry points validate identifiers and quantities before retrieving facts.
    result = method(*args)
    logger.info("tool=%s entity=%r status=%s", tool_name, args[0], result.status.value)
    return result


def check_po(po_no, *, engine=None):
    return _invoke("check_po", (engine or RulesEngine()).evaluate_purchase_order, po_no)


def check_stock(part_no, requested_qty, *, engine=None):
    return _invoke(
        "check_stock", (engine or RulesEngine()).evaluate_stock_position, part_no, requested_qty
    )


def check_dealer(dealer_id, *, engine=None):
    return _invoke("check_dealer", (engine or RulesEngine()).evaluate_dealer_eligibility, dealer_id)


def check_part(part_no, *, engine=None):
    return _invoke("check_part", (engine or RulesEngine()).evaluate_part_eligibility, part_no)


def check_claim(claim_id, *, engine=None):
    return _invoke("check_claim", (engine or RulesEngine()).evaluate_claim_consistency, claim_id)


def check_warranty(claim_id, *, engine=None):
    return _invoke("check_warranty", (engine or RulesEngine()).evaluate_warranty, claim_id)


def scan_anomalies(*, engine=None):
    result = (engine or RulesEngine()).scan_known_data_quality_issues()
    logger.info("tool=scan_anomalies issues=%d", len(result))
    return result


def plan_fulfillment(part_no=None, requested_qty=None, *, po_id=None, repository=None):
    if po_id is not None:
        if part_no is not None or requested_qty is not None:
            raise ValueError("Provide either PO or part/quantity inputs.")
        return build_fulfillment_plan_for_po(po_id, repository=repository)
    return build_fulfillment_plan(part_no, requested_qty, repository=repository)
