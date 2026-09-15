"""Streamlit presentation and session interactions; business decisions stay in tools."""

from datetime import datetime, timezone
import logging

import streamlit as st

from fulfillment import FulfillmentPlan
from rules import DecisionResult, DecisionStatus
from intent import parse_intent
from llm_client import LLMClient, get_llm_status
from repositories import RepositoryError
from responder import generate_response, fulfillment_facts
from router import execute_intent
import tools
from ui_models import dataset_view, result_view

logger = logging.getLogger(__name__)
ACTIONS = {
    "Evaluate Purchase Order": (tools.check_po, "evaluate_purchase_order", "PO ID", "po_id"),
    "Check Stock": (tools.check_stock, "evaluate_stock_position", "Part Number", "stock_part"),
    "Check Dealer": (tools.check_dealer, "evaluate_dealer_eligibility", "Dealer ID", "dealer_id"),
    "Check Part": (tools.check_part, "evaluate_part_eligibility", "Part Number", "part_no"),
    "Check Claim": (tools.check_claim, "evaluate_claim_consistency", "Claim ID", "claim_id"),
    "Scan Operational Risks": (tools.scan_anomalies, "scan_known_data_quality_issues", None, None),
}


def run_copilot(user_text, client):
    """Submit once; return display data with provenance exclusively from the tool."""
    if get_llm_status().state != "Configured":
        return {
            "message": "Natural-language interpretation requires configured AI. "
            "Use the guided checks below.", "view": None,
        }
    parsed = parse_intent(user_text, client)
    execution = execute_intent(parsed)
    response = generate_response(user_text, execution, client)
    view = None
    if execution.result is not None and execution.error is None:
        display_result = execution.result
        if isinstance(display_result, FulfillmentPlan):
            styles = {"FULLY_FULFILLABLE": DecisionStatus.PASS,
                      "PARTIALLY_FULFILLABLE": DecisionStatus.WARNING,
                      "NO_STOCK": DecisionStatus.WARNING, "BLOCKED": DecisionStatus.BLOCKED}
            display_result = DecisionResult(
                styles[execution.result.status.value], "Fulfillment Plan — proposed only; nothing shipped.",
                fulfillment_facts(execution.result, execution.po_id),
                execution.result.issues, execution.result.evidence,
            )
        view = result_view(
            display_result, action=parsed.intent,
            tool=execution.tool_name, check=execution.tool_name,
        )
        if isinstance(execution.result, FulfillmentPlan):
            view["status"]["label"] = execution.result.status.value
            view["checks"][0]["Outcome"] = execution.result.status.value
            view["fulfillment"] = fulfillment_facts(execution.result, execution.po_id)
    return {"message": response.text, "view": view, "intent": parsed.intent}


def _prefill(action, key=None, value=None, qty=None):
    st.session_state["action"] = action
    if key:
        st.session_state[key] = value
    if qty is not None:
        st.session_state["requested_qty"] = qty


def render_dataset_health(metadata):
    view = dataset_view(metadata)
    with st.expander("Dataset Health", expanded=False):
        st.text(f"Source filename: {view['source']}")
        st.text(f"Dataset fingerprint: {view['fingerprint']}")
        st.write(f"Operational table count: {view['table_count']}")
        st.write(f"Total operational rows: {view['row_count']}")
        st.write(f"Validation error count: {view['errors']}")
        st.write(f"Validation warning count: {view['warnings']}")
        st.dataframe(view["tables"], hide_index=True, width="stretch")


def _render_result(view):
    st.caption("DETERMINISTIC COPILOT RESULT")
    getattr(st, view["status"]["style"])(view["status"]["label"])
    st.text(view["summary"])
    if view.get("fulfillment"):
        facts = view["fulfillment"]
        st.subheader("Fulfillment Plan")
        for label, key in (("Requested", "requested_qty"), ("Network Available", "network_available_qty"),
                           ("Planned Fulfillment", "planned_fulfillment_qty"),
                           ("Remaining", "unresolved_remaining_qty")):
            st.metric(label, facts[key] if facts[key] is not None else "Unavailable")
        st.dataframe(facts["allocations"], hide_index=True, width="stretch")
    if view.get("dealers"):
        st.dataframe(view["dealers"], hide_index=True, width="stretch")
    for stock in view["stocks"]:
        st.text(f"Stock position · {stock['part_no']}")
        columns = st.columns(3)
        for column, label, key in zip(
            columns,
            ("Requested", "Available", "Deficit"),
            ("requested_qty", "available_qty", "deficit_qty"),
        ):
            column.metric(label, stock[key])
    with st.expander("Facts", expanded=True):
        if view["facts"]:
            st.dataframe(view["facts"], hide_index=True, width="stretch", height="auto")
        else:
            st.caption("No source facts were returned for this check.")
    with st.expander(f"Issues ({len(view['issues'])})", expanded=True):
        if view["issues"]:
            st.dataframe(view["issues"], hide_index=True, width="stretch")
        else:
            st.caption("No issues reported by this check.")


def _render_trace(record):
    st.subheader("Evidence & Decision Trace")
    st.caption("Factual provenance and check outcomes from the deterministic engine.")
    if record is None:
        st.info("Run a check to see the source records and decision outcome.")
        return
    view = record["view"]
    if record.get("intent"):
        st.text(f"Parsed intent: {record['intent']}")
    st.text(f"Action: {view['action']}")
    st.text(f"Tool: {view['tool']}")
    st.text(f"Outcome: {view['status']['label']}")
    st.caption(f"Evaluated: {record['time']} · Dataset: {record['fingerprint']}")
    st.dataframe(view["checks"], hide_index=True, width="stretch")
    st.markdown("**Source records**")
    if view["evidence"]:
        st.dataframe(view["evidence"], hide_index=True, width="stretch")
    else:
        st.caption("No source records returned. Missing records are not replaced with sample data.")
    if view["issues"]:
        st.markdown("**Issue codes**")
        st.text(", ".join(dict.fromkeys(i["Code"] for i in view["issues"])))


def render_command_center(metadata):
    if st.session_state.get("dataset_sha") != metadata["sha256"]:
        st.session_state["history"] = []
        st.session_state["latest"] = None
        st.session_state["copilot_notice"] = None
        st.session_state["dataset_sha"] = metadata["sha256"]
    left, right = st.columns([1.25, 1], gap="large")
    with left:
        st.subheader("Copilot Command Center")
        st.subheader("Ask DealerBRAIN")
        with st.form("copilot", clear_on_submit=True):
            question = st.text_input(
                "Your request", max_chars=4000,
                placeholder="Ask about a PO, part, dealer, claim, stock, or operational risks...",
            )
            ask = st.form_submit_button("Ask DealerBRAIN")
        if ask:
            st.session_state["latest"] = None
            st.session_state["copilot_notice"] = None
            try:
                with st.spinner("Checking your request…"):
                    interaction = run_copilot(question, LLMClient())
                if interaction["view"] is None:
                    st.session_state["copilot_notice"] = interaction["message"]
                else:
                    record = {
                        **interaction, "inputs": [],
                        "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                        "fingerprint": metadata["sha256"][:16],
                    }
                    st.session_state["latest"] = record
                    st.session_state["history"] = (st.session_state["history"] + [record])[-20:]
            except (TypeError, ValueError, RepositoryError):
                st.session_state["copilot_notice"] = (
                    "This request could not be displayed safely. Use the guided checks below."
                )
        if st.session_state.get("copilot_notice"):
            st.info(st.session_state["copilot_notice"])
        st.caption("Choose a check. Decisions come from Python rules and operational records.")
        with st.expander("Demo Scenarios", expanded=False):
            st.caption("Shortcuts fill inputs only. Select Run check to evaluate live data.")
            for po in ("PO-2026-1026", "PO-2026-1106"):
                st.button(
                    f"Evaluate {po}",
                    on_click=_prefill,
                    args=("Evaluate Purchase Order", "po_id", po),
                    width="stretch",
                )
            st.button(
                "Stock: P-10036 · quantity 20",
                on_click=_prefill,
                args=("Check Stock", "stock_part", "P-10036", 20),
                width="stretch",
            )
            st.button(
                "Scan Operational Risks",
                on_click=_prefill,
                args=("Scan Operational Risks",),
                width="stretch",
            )
        action = st.selectbox("Action", list(ACTIONS), key="action")
        function, check, label, key = ACTIONS[action]
        with st.form("command"):
            inputs = []
            if label:
                inputs.append(st.text_input(label, key=key))
            if action == "Check Stock":
                inputs.append(
                    st.number_input("Requested Quantity", value=1, step=1, key="requested_qty")
                )
            if not label:
                st.caption("Scan operational records for supported data-quality risks.")
            submitted = st.form_submit_button("Run check", type="primary", width="stretch")
        if submitted:
            st.session_state["copilot_notice"] = None
            # Clear the previous display before attempting a new result, including on errors.
            st.session_state["latest"] = None
            try:
                with st.spinner("Evaluating operational data…"):
                    result = function(*inputs)
                view = result_view(result, action=action, tool=function.__name__, check=check)
                record = {
                    "view": view,
                    "inputs": inputs,
                    "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "fingerprint": metadata["sha256"][:16],
                }
                st.session_state["latest"] = record
                st.session_state["history"] = (st.session_state["history"] + [record])[-20:]
            except RepositoryError:
                logger.warning("Repository failed during tool=%s", function.__name__)
                st.error(
                    "The operational data could not be read reliably. Check the database "
                    "and initialization, then retry. No decision was produced."
                )
            except (TypeError, ValueError):
                logger.warning("Invalid input or unsupported result for tool=%s", function.__name__)
                st.error("This check could not be displayed safely. Verify the inputs and retry.")
        latest = st.session_state.get("latest")
        if latest:
            if latest.get("message"):
                st.text(latest["message"])
            input_label = ", ".join(str(i) for i in latest["inputs"]) or latest.get(
                "intent", "Operational scan"
            )
            st.caption(
                f"Last completed check: {latest['view']['action']} · "
                f"Inputs: {input_label}"
            )
            _render_result(latest["view"])
    with right:
        _render_trace(st.session_state.get("latest"))
    with st.expander("Session History", expanded=False):
        history = st.session_state["history"]
        if history:
            st.dataframe(
                [
                    {
                        "Time (UTC)": r["time"],
                        "Action": r["view"]["action"],
                        "Inputs": ", ".join(str(v) for v in r["inputs"]),
                        "Status": r["view"]["status"]["label"],
                        "Evidence records": len(r["view"]["evidence"]),
                    }
                    for r in reversed(history)
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("Completed checks will appear here for this session only.")
        st.caption("Last 20 checks. History is not written to a database.")
