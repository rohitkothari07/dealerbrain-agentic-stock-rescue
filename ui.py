"""Streamlit presentation and session interactions; business decisions stay in tools."""

from datetime import datetime, timezone
from html import escape
import logging
import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from fulfillment import FulfillmentPlan
from rules import DecisionResult, DecisionStatus
from intent import ParsedIntent, classify_general_chat, parse_intent
from llm_client import LLMClient, get_llm_status
from repositories import Repository, RepositoryError
from responder import (
    generate_followup_response, generate_general_response, generate_response, fulfillment_facts,
    knowledge_facts,
)
from rag import KnowledgeRetrieval
from router import execute_intent
import tools
import transactions
from ui_models import dataset_view, inventory_dashboard_view, result_view
import vision

logger = logging.getLogger(__name__)
ACTIONS = {
    "Evaluate Purchase Order": (tools.check_po, "evaluate_purchase_order", "PO ID", "po_id"),
    "Check Stock": (tools.check_stock, "evaluate_stock_position", "Part Number", "stock_part"),
    "Check Dealer": (tools.check_dealer, "evaluate_dealer_eligibility", "Dealer ID", "dealer_id"),
    "Check Part": (tools.check_part, "evaluate_part_eligibility", "Part Number", "part_no"),
    "Check Claim": (tools.check_claim, "evaluate_claim_consistency", "Claim ID", "claim_id"),
    "Scan Operational Risks": (tools.scan_anomalies, "scan_known_data_quality_issues", None, None),
    "Plan Fulfillment": (tools.plan_fulfillment, "plan_fulfillment", "PO ID", "fulfillment_po"),
}


QUICK_PROMPTS = (
    "Can PO-2026-1026 be fulfilled?",
    "Process PO-2026-1106",
    "Show stock for part P-10036",
    "What is the procedure for a suspended dealer?",
)


KNOWLEDGE_EXAMPLES = (
    "What is the procedure for a suspended dealer?",
    "What should we do with negative available stock?",
    "What does the guidance say about hazmat shipping?",
)


_DARK_THEME = {
    "bg": "#07111f", "sidebar": "#0b1729", "surface": "#101f37", "surface2": "#152947",
    "border": "#29486f", "text": "#edf5ff", "muted": "#bccee6", "cyan": "#36ddff",
    "blue": "#1677ff", "green": "#28e38a", "amber": "#ffd24a", "pink": "#ff6db2",
    "accent-line": "#367b95", "status-bg": "#12352e", "status-text": "#78e4b4",
    "warn-bg": "#282716", "warn-border": "#b88922", "nav-text": "#c5d6ec",
    "shadow": "0 1px 0 rgba(255,255,255,.03) inset, 0 6px 18px rgba(0,0,0,.18)",
}
_LIGHT_THEME = {
    "bg": "#f4f6fb", "sidebar": "#ffffff", "surface": "#ffffff", "surface2": "#eef2f9",
    "border": "#d7e0ee", "text": "#101a2b", "muted": "#51617a", "cyan": "#0e7fa3",
    "blue": "#1677ff", "green": "#0f7a52", "amber": "#9c6a08", "pink": "#c22a76",
    "accent-line": "#7ea9c9", "status-bg": "#e3f6ec", "status-text": "#0f7a52",
    "warn-bg": "#fff2d6", "warn-border": "#b88922", "nav-text": "#33455e",
    "shadow": "0 1px 0 rgba(255,255,255,.7) inset, 0 4px 14px rgba(20,30,50,.08)",
}


def _theme_variables(theme):
    tokens = _LIGHT_THEME if theme == "light" else _DARK_THEME
    return "".join(f"--{name}:{value};" for name, value in tokens.items())


def render_theme_toggle():
    """Top-right dark/light switch; a display preference only, no business behavior."""
    is_dark = st.session_state.get("ui_theme", "light") == "dark"
    with st.container(key="theme_toggle"):
        choice = st.toggle(
            "🌙 Dark" if is_dark else "☀️ Light", value=is_dark, key="ui_theme_is_dark",
            help="Switch between dark and light display mode.",
        )
    st.session_state["ui_theme"] = "dark" if choice else "light"


# Purely a client-side engagement prompt: no business facts, no server round-trip.
# Deliberately NOT persisted to localStorage/sessionStorage — every fresh page load
# (including after restarting the app) gets its own clean 2-minute countdown. A flag
# on the parent document object (not Storage) only guards against Streamlit's mid-session
# reruns — which rerun this whole script on every chat/button interaction — scheduling
# duplicate timers; that flag lives only in memory and disappears on the next real page load.
_ENGAGEMENT_POPUP_HTML = """
<script>
(function () {
    var doc = window.parent.document;
    if (doc.getElementById('db-engagement-popup') || doc.__dbEngagementPopupScheduled) return;
    doc.__dbEngagementPopupScheduled = true;
    var DELAY_MS = 120000;
    var VISIBLE_MS = 120000;

    function show() {
        if (doc.getElementById('db-engagement-popup')) return;
        var wrap = doc.createElement('div');
        wrap.id = 'db-engagement-popup';
        wrap.style.cssText =
            'position:fixed;right:22px;bottom:22px;z-index:999999;width:310px;' +
            'background:#ffffff;color:#101a2b;border-radius:16px;overflow:hidden;' +
            'border:1px solid #e3e8f0;box-shadow:0 14px 36px rgba(15,23,42,.24);' +
            'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;' +
            'opacity:0;transform:translateY(14px);transition:opacity .35s ease,transform .35s ease;';
        wrap.innerHTML =
            '<div style="display:flex;align-items:center;gap:10px;padding:13px 14px;' +
            'background:linear-gradient(135deg,#1677ff,#36ddff);">' +
              '<div style="width:32px;height:32px;border-radius:9px;background:#ffffff;' +
              'display:flex;align-items:center;justify-content:center;font-weight:800;' +
              'color:#1677ff;font-size:13px;flex:none;">DB</div>' +
              '<div style="color:#ffffff;font-weight:700;font-size:14px;">DealerBRAIN</div>' +
              '<div id="db-popup-close" style="margin-left:auto;cursor:pointer;color:#ffffff;' +
              'opacity:.9;font-size:18px;line-height:1;padding:2px 4px;">&times;</div>' +
            '</div>' +
            '<div style="padding:16px 14px 15px;">' +
              '<div style="font-size:14.5px;font-weight:600;margin-bottom:13px;line-height:1.4;">' +
              'Enjoying DealerBRAIN?</div>' +
              '<div style="display:flex;gap:9px;">' +
                '<button id="db-popup-yes" style="flex:1;padding:9px 0;border-radius:9px;' +
                'border:1px solid #1677ff;background:#1677ff;color:#fff;font-weight:600;' +
                'font-size:13px;cursor:pointer;">Yes</button>' +
                '<button id="db-popup-no" style="flex:1;padding:9px 0;border-radius:9px;' +
                'border:1px solid #d7e0ee;background:#fff;color:#101a2b;font-weight:600;' +
                'font-size:13px;cursor:pointer;">No</button>' +
              '</div>' +
            '</div>';
        doc.body.appendChild(wrap);
        requestAnimationFrame(function () {
            wrap.style.opacity = '1';
            wrap.style.transform = 'translateY(0)';
        });
        var autoHide = setTimeout(dismiss, VISIBLE_MS);
        function dismiss() {
            clearTimeout(autoHide);
            wrap.style.opacity = '0';
            wrap.style.transform = 'translateY(14px)';
            setTimeout(function () { wrap.remove(); }, 350);
        }
        doc.getElementById('db-popup-yes').onclick = dismiss;
        doc.getElementById('db-popup-no').onclick = dismiss;
        doc.getElementById('db-popup-close').onclick = dismiss;
    }

    setTimeout(show, DELAY_MS);
})();
</script>
"""


def render_engagement_popup():
    """Bottom-right satisfaction prompt, 2 minutes after page load; UI-only, no business data."""
    components.html(_ENGAGEMENT_POPUP_HTML, height=0)


def render_demo_shell(metadata, llm_status):
    """Static styling and truthful readiness; no service probes or business actions."""
    theme = st.session_state.get("ui_theme", "light")
    st.markdown(f"<style>:root{{{_theme_variables(theme)}}}</style>", unsafe_allow_html=True)
    st.markdown("""<style>
    :root {--font:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;}
    html, body, [data-testid="stAppViewContainer"] {font-family:var(--font);font-size:16px;}
    [data-testid="stAppViewContainer"], [data-testid="stHeader"] {background:var(--bg);color:var(--text);}
    [data-testid="stSidebar"] {background:var(--sidebar);color:var(--text);border-right:1px solid var(--border);}
    .stMainBlockContainer {max-width:1280px;padding:2.6rem 2.2rem 3.5rem;}
    h1 {font-size:2rem!important;letter-spacing:-.03em;padding-bottom:.3rem!important;line-height:1.3!important;}
    h2, h3 {letter-spacing:-.01em;line-height:1.4!important;}
    h1,h2,h3,p,[data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"] {color:var(--text);font-family:var(--font);}
    p, [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li {font-size:1rem;line-height:1.75;}
    [data-testid="stCaptionContainer"] {color:var(--muted);font-size:.92rem;line-height:1.6;}
    /* Chat messages: generous spacing and a proportional, readable font so replies never feel cramped. */
    [data-testid="stChatMessage"], [data-testid="stMetric"], [data-testid="stVerticalBlockBorderWrapper"] {background:var(--surface);border-radius:16px;color:var(--text);}
    [data-testid="stChatMessage"] {border:0;padding:1.6rem 1.75rem;margin:.6rem 0 1.75rem;box-shadow:var(--shadow);}
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {background:var(--surface2);margin-left:10%;padding:1.05rem 1.35rem;}
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {border-left:3px solid var(--accent-line);}
    [data-testid="stChatMessage"] h3 {font-size:1.15rem;padding:.5rem 0;line-height:1.5!important;}
    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {font-size:1.03rem;line-height:1.8;margin-bottom:.6rem;}
    [data-testid="stChatMessage"] [data-testid="stText"],
    [data-testid="stChatMessage"] [data-testid="stText"] pre {
        font-family:var(--font)!important;font-size:1.03rem!important;line-height:1.8!important;
        white-space:pre-wrap!important;word-break:break-word;overflow-wrap:anywhere;
        background:transparent!important;border:0!important;padding:0!important;margin:0 0 .3rem 0!important;
        color:var(--text)!important;
    }
    [data-testid="stChatMessage"] [data-testid="stCaptionContainer"] {margin-top:.5rem;}
    [data-testid="stMetric"] {border:1px solid var(--border);padding:.6rem .8rem;}
    [data-testid="stMetricLabel"] {font-size:.92rem;}
    [data-testid="stMetricValue"] {color:var(--text);font-size:2rem;}
    [data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button {border-radius:10px;border:1px solid var(--border);background:var(--surface);color:var(--text);min-height:2.7rem;font-size:.95rem;transition:border-color .15s ease, background .15s ease;}
    .st-key-quick_prompts button p {font-size:.85rem;line-height:1.5;white-space:normal;}
    .st-key-quick_prompts button {height:4.4rem;padding:.7rem .9rem;}
    [data-testid="stButton"] button:hover {border-color:var(--cyan);background:var(--surface2);}
    [data-testid="stFormSubmitButton"] button {background:var(--blue);border-color:var(--blue);font-weight:700;}
    [data-testid="stChatInput"] textarea {font-size:1rem;line-height:1.6;padding:.85rem 1rem;}
    [data-testid="stChatInput"] textarea, [data-baseweb="input"] input,[data-baseweb="select"] > div {background:var(--surface);color:var(--text);border-color:var(--border);}
    [data-testid="stChatInput"] {background:var(--surface);border:1px solid var(--border);border-radius:14px;}
    [data-testid="stExpander"] {margin:.5rem 0;}
    [data-testid="stExpander"] details {background:transparent;border:0;border-top:1px solid var(--border);border-radius:0;}
    [data-testid="stExpander"] summary {padding:.9rem 0;font-size:.98rem;}
    [data-testid="stExpander"] .streamlit-expanderContent {padding:.75rem 0 1.25rem;}
    [data-testid="stSidebar"] [data-testid="stButton"] button {text-align:left;border-color:transparent;background:transparent;min-height:2.8rem;}
    [data-testid="stSidebar"] [data-testid="stButton"] button:hover {border-color:var(--border);}
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap:.6rem;}
    [data-testid="stVerticalBlock"] {gap:1.1rem;}
    [data-testid="stHorizontalBlock"] {gap:1.25rem;}
    .db-card {background:var(--surface2);border:0;border-radius:14px;padding:20px 22px;margin:6px 0;line-height:1.65;}
    .db-metric, .db-allocation {min-height:158px;box-sizing:border-box;}
    .db-metric > b {font-size:1.65rem;line-height:1.6;}
    .db-status {background:var(--status-bg);line-height:1.7;}
    .db-status b {color:var(--status-text);font-size:1.18rem;}
    .db-amber {border-left:3px solid var(--warn-border);background:var(--warn-bg);}
    .db-support {border-top:1px solid var(--border);padding:22px 0;line-height:1.8;font-size:.96rem;}
    .db-pink {color:var(--pink);}
    .db-logo {font-size:1.45rem;font-weight:800}.db-logo span{color:var(--cyan)}
    .db-small {color:var(--muted);font-size:.86rem;line-height:1.6;}.db-rail-title{color:var(--cyan);font-weight:700;letter-spacing:.04em}
    .db-nav {padding:10px 14px;margin:2px 0;border-radius:10px;color:var(--nav-text);font-size:.95rem;}.db-nav-active {background:var(--blue);color:white;font-weight:700}
    .st-key-theme_toggle {display:flex;justify-content:flex-end;margin-top:.3rem;}
    @media (max-width: 800px) {
        .stMainBlockContainer {padding: 1.25rem;}
        [data-testid="stHorizontalBlock"] {flex-wrap: wrap;}
        [data-testid="stColumn"] {min-width: min(100%, 220px); flex: 1 1 220px;}
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {margin-left:0;}
    }
    </style>""", unsafe_allow_html=True)
    with st.sidebar:
        st.markdown('<div class="db-logo">Dealer<span>BRAIN</span></div><div class="db-small">Team Stock Overflow · i.mobilothon 6.0</div>', unsafe_allow_html=True)
        st.divider()
        for index, label in enumerate(("💬  Chat", "📚  Knowledge", "📦  Inventory", "📋  POs", "🛡  Governance", "📊  Audit Trail")):
            active = " db-nav-active" if index == 0 else ""
            st.markdown(f'<div class="db-nav{active}">{label}</div>', unsafe_allow_html=True)
        st.divider()
        st.markdown("**POC Mode · human approval**")
        st.caption(f"AI: {llm_status.state}")
        tables = {item["table"] for item in metadata["tables"]}
        inventory = "✓ Inventory data ready" if "inventory" in tables else "• Inventory data unavailable"
        governance = ("✓ Governance rules ready" if all(callable(a[0]) for a in ACTIONS.values())
                      else "• Governance rules unavailable")
        knowledge = "✓ Knowledge base ready" if "knowledge" in tables else "• Knowledge base unavailable"
        copilot = ("✓ Copilot configured" if llm_status.state == "Configured"
                   else "• Copilot unavailable — guided checks ready")
        with st.expander("System status", expanded=False):
            st.caption(f"{inventory}\n\n{governance}\n\n{knowledge}\n\n{copilot}")
        with st.expander("About & trust"):
            st.write("AI understands and explains. Deterministic services establish business truth. "
                     "Humans authorize consequential actions.")
            st.caption("Built by Team Stock Overflow for i.mobilothon 6.0. "
                       "Hackathon prototype; not an official production Volkswagen product.")


def control_tower_metrics(metadata):
    view = dataset_view(metadata)
    return {"Operational tables": view["table_count"], "Operational rows": view["row_count"],
            "Validation warnings": view["warnings"]}


def render_control_tower(metadata):
    st.subheader("After-Sales Control Tower")
    metrics = control_tower_metrics(metadata)
    for column, (label, value) in zip(st.columns(3, gap="medium"), metrics.items()):
        with column.container(border=True):
            st.metric(label, value)
    st.caption("Source data: read-only · Writes: human-confirmed POC simulations only · "
               "AI: grounded in deterministic tools and Knowledge evidence")


def render_inventory_dashboard():
    """Read-only stock/inventory dashboard; a display join of two deterministic reads."""
    st.subheader("📦 Inventory & Stock Dashboard")
    st.caption(
        "Live read from the operational inventory and parts catalog. Source data is "
        "read-only; flagged positions are shown as-is, never silently corrected."
    )
    try:
        repository = Repository()
        inventory_result = repository.list_inventory()
        parts_result = repository.list_parts()
    except RepositoryError:
        st.info("Inventory data is unavailable. Check the database and initialization.")
        return
    view = inventory_dashboard_view(inventory_result, parts_result)
    summary = view["summary"]
    metric_specs = (
        ("Distinct Parts Tracked", summary["distinct_parts"]),
        ("Total On-Hand Units", summary["total_on_hand"]),
        ("Total Available Units", summary["total_available"]),
        ("Warehouses", summary["warehouse_count"]),
    )
    for column, (label, value) in zip(st.columns(4, gap="medium"), metric_specs):
        with column.container(border=True):
            st.metric(label, value)
    if summary["negative_available_count"]:
        st.warning(
            f"{summary['negative_available_count']} inventory position(s) show negative "
            "available stock — preserved from source data, not corrected."
        )
    if summary["below_reorder_count"]:
        st.caption(
            f"{summary['below_reorder_count']} position(s) are at or below their reorder point."
        )
    if view["warehouses"]:
        st.markdown("**Stock by warehouse**")
        chart, table = st.columns([0.55, 0.45], gap="medium")
        chart_data = pd.DataFrame(view["warehouses"]).set_index("Warehouse")[["On Hand", "Available"]]
        chart.bar_chart(chart_data, height=280)
        table.dataframe(view["warehouses"], hide_index=True, width="stretch", height=280)
    if view["categories"]:
        with st.expander("Stock by category", expanded=False):
            st.dataframe(view["categories"], hide_index=True, width="stretch")
    st.markdown("**Inventory positions**")
    warehouse_options = ["All"] + sorted({r["Warehouse"] for r in view["rows"]})
    category_options = ["All"] + sorted({r["Category"] for r in view["rows"]})
    warehouse_col, category_col, search_col = st.columns(3, gap="medium")
    warehouse_filter = warehouse_col.selectbox("Warehouse", warehouse_options, key="inv_dash_warehouse")
    category_filter = category_col.selectbox("Category", category_options, key="inv_dash_category")
    search = search_col.text_input("Search part no. or name", key="inv_dash_search")
    rows = view["rows"]
    if warehouse_filter != "All":
        rows = [r for r in rows if r["Warehouse"] == warehouse_filter]
    if category_filter != "All":
        rows = [r for r in rows if r["Category"] == category_filter]
    if search.strip():
        needle = search.strip().lower()
        rows = [
            r for r in rows
            if needle in r["Part No"].lower() or needle in r["Part Name"].lower()
        ]
    st.dataframe(rows, hide_index=True, width="stretch", height=420)
    st.caption(f"Showing {len(rows)} of {len(view['rows'])} inventory position(s).")


def _prefill_question(question):
    st.session_state["copilot_question"] = question


def _followup_kind(user_text):
    normalized = " ".join(user_text.lower().split()) if isinstance(user_text, str) else ""
    if normalized in {"why", "why?", "can you explain that simply?", "explain that simply"} or normalized.startswith("why "):
        return "EXPLAIN"
    if normalized in {"what should i do next?", "what should i do next", "what next?", "what next"}:
        return "NEXT"
    return None


def run_copilot(user_text, client, previous=None):
    """Submit once; return display data with provenance exclusively from the tool."""
    followup = _followup_kind(user_text)
    previous_execution = previous.get("execution") if isinstance(previous, dict) else None
    if followup and previous_execution is not None:
        response = generate_followup_response(user_text, previous_execution, client, followup)
        return {
            "message": response.text, "view": previous.get("view"), "intent": "FOLLOW_UP",
            "plan": previous.get("plan"), "po_id": previous.get("po_id"),
            "execution": previous_execution,
        }
    kind = classify_general_chat(user_text)
    if kind:
        parsed = ParsedIntent("GENERAL_CHAT", conversation_kind=kind)
        execution = execute_intent(parsed)
        if get_llm_status().state == "Configured":
            response = generate_general_response(user_text, client, kind)
        else:
            response = generate_general_response(user_text, None, kind)
        return {"message": response.text, "view": None, "intent": parsed.intent,
                "plan": None, "po_id": None, "execution": execution}
    if get_llm_status().state != "Configured":
        return {
            "message": "Natural-language interpretation requires configured AI. "
            "Use the guided checks below.", "view": None,
        }
    parsed = parse_intent(user_text, client)
    execution = execute_intent(parsed)
    response = (generate_general_response(user_text, client, parsed.conversation_kind)
                if parsed.intent == "GENERAL_CHAT"
                else generate_response(user_text, execution, client))
    view = None
    if execution.result is not None and execution.error is None:
        view = _execution_view(execution.result, parsed.intent, execution.tool_name, execution.po_id)
    return {"message": response.text, "view": view, "intent": parsed.intent,
            "plan": execution.result if isinstance(execution.result, FulfillmentPlan) else None,
            "po_id": execution.po_id, "execution": execution}


def _execution_view(result, action, tool, po_id=None, check=None):
    display = result
    if isinstance(result, KnowledgeRetrieval):
        display = DecisionResult(
            DecisionStatus.PASS if result.status == "MATCHED" else DecisionStatus.NOT_APPLICABLE,
            "Retrieved Knowledge source records; retrieval score is lexical relevance.",
            knowledge_facts(result), (), tuple(m.evidence for m in result.matches),
        )
    if isinstance(result, FulfillmentPlan):
        styles = {"FULLY_FULFILLABLE": DecisionStatus.PASS,
                  "PARTIALLY_FULFILLABLE": DecisionStatus.WARNING,
                  "NO_STOCK": DecisionStatus.WARNING, "BLOCKED": DecisionStatus.BLOCKED}
        display = DecisionResult(
            styles[result.status.value], "Fulfillment Plan — proposed only; nothing shipped.",
            fulfillment_facts(result, po_id), result.issues, result.evidence,
        )
    view = result_view(display, action=action, tool=tool, check=check or tool)
    if isinstance(result, FulfillmentPlan):
        view["status"]["label"] = result.status.value
        view["checks"][0]["Outcome"] = result.status.value
        view["fulfillment"] = fulfillment_facts(result, po_id)
    if isinstance(result, KnowledgeRetrieval):
        view["status"]["label"] = result.status
        view["checks"][0]["Outcome"] = result.status
        view["knowledge"] = knowledge_facts(result)["records"]
    return view


def _render_action(record):
    plan = record.get("plan")
    if not isinstance(plan, FulfillmentPlan) or plan.fulfilled_qty <= 0:
        return
    st.subheader("Proposed Fulfillment Action")
    st.markdown('<div class="db-card db-amber"><b>Recommended next step</b><br>Review the allocation, then confirm the <b>POC_SIMULATED</b> plan.<br><span class="db-small">Human approval required • No real system changes</span></div>', unsafe_allow_html=True)
    st.caption("POC_SIMULATED · no ERP, inventory, shipment, or PO source data will be modified.")
    if st.button("👤 Request Human Approval", key="confirm_simulated_fulfillment", type="primary"):
        record["action_result"] = transactions.execute_simulated_fulfillment(
            record["po_id"], plan, confirmed=True,
        )
    outcome = record.get("action_result")
    if outcome:
        if outcome.action is None:
            st.error(outcome.error or "Simulated action rejected.")
        else:
            action = outcome.action
            st.success("Existing simulated action shown." if outcome.status == "ALREADY_EXISTS"
                       else "Simulated fulfillment action recorded.")
            st.text(f"{action.action_id} · {action.status} · {outcome.status}")
            st.text(f"Planned quantity: {action.planned_qty} · Remaining unresolved: {action.remaining_qty}")
            st.text(f"Created: {action.created_at}")
            st.dataframe([{"Source location": a.source_location, "Proposed quantity": a.proposed_qty}
                          for a in action.allocations], hide_index=True)


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
    status = view["status"]["label"]
    labels = {
        "FULLY_FULFILLABLE": "FULLY FULFILLABLE — full quantity can be planned",
        "PARTIALLY_FULFILLABLE": "PARTIAL FULFILLMENT — unresolved demand remains",
        "NO_STOCK": "No stock available to allocate",
        "BLOCKED": "Blocked — governance requirements are not met",
    }
    if not view.get("fulfillment") or status not in {"FULLY_FULFILLABLE", "PARTIALLY_FULFILLABLE"}:
        getattr(st, view["status"]["style"])(labels.get(status, status))
        st.caption(view["summary"])
    if view.get("fulfillment"):
        facts = view["fulfillment"]
        planned = facts["planned_fulfillment_qty"]
        requested = facts["requested_qty"]
        if status in {"FULLY_FULFILLABLE", "PARTIALLY_FULFILLABLE"}:
            heading = "Full fulfillment" if status == "FULLY_FULFILLABLE" else "Partial fulfillment"
            st.markdown(f'<div class="db-card db-status"><b>{heading}</b><br>{planned} of {requested} units can be fulfilled from network inventory.</div>', unsafe_allow_html=True)
        cards = st.columns(4, gap="medium")
        metric_specs = (
            ("", requested, "Requested Quantity", f"for {facts.get('po_id') or 'this request'}"),
            ("", planned, "Planned Quantity", f"{facts['network_available_qty']} network available"),
            ("", facts["unresolved_remaining_qty"], "Unresolved Quantity", "shortage"),
        )
        for column, (icon, value, label, detail) in zip(cards[:3], metric_specs):
            accent = ' db-pink' if label == "Unresolved Quantity" else ''
            column.markdown(f'<div class="db-card db-metric"><b>{icon} <span class="{accent.strip()}">{escape(str(value))}</span></b><br>{escape(label)}<br><span class="db-small">{escape(detail)}</span></div>', unsafe_allow_html=True)
        allocations = facts["allocations"]
        allocation_lines = "<br>".join(
            f"{escape(str(item['source_location']))}: {escape(str(item['proposed_qty']))} units"
            for item in allocations
        ) or "No allocation proposed"
        cards[3].markdown(f'<div class="db-card db-allocation"><b>Allocation Plan</b><br><span class="db-small">{allocation_lines}</span></div>', unsafe_allow_html=True)
    if view["issues"]:
        st.caption("Issues / governance: " + ", ".join(dict.fromkeys(i["Code"] for i in view["issues"])))
    for stock in view["stocks"]:
        st.text(f"Stock position · {stock['part_no']}")
        columns = st.columns(3, gap="medium")
        for column, label, key in zip(
            columns,
            ("Requested", "Available", "Deficit"),
            ("requested_qty", "available_qty", "deficit_qty"),
        ):
            column.metric(label, stock[key])


def _render_details(view):
    """Secondary source detail; the outcome and issue codes stay visible above."""
    with st.expander("Facts & details", expanded=False):
        st.caption(view["summary"])
        if view.get("knowledge"):
            st.dataframe(view["knowledge"], hide_index=True)
        if view.get("dealers"):
            st.dataframe(view["dealers"], hide_index=True, width="stretch")
        if view["facts"]:
            st.dataframe(view["facts"], hide_index=True, width="stretch", height="auto")
        else:
            st.caption("No source facts were returned for this check.")
        if view["issues"]:
            st.markdown("**Issues / governance**")
            st.dataframe(view["issues"], hide_index=True, width="stretch")



def _display_evidence(rows):
    """Copy presentation rows; preserve authoritative keys in the underlying view."""
    displayed = []
    for row in rows:
        copy = dict(row)
        if row["Source table"] == "purchase_orders":
            try:
                key = json.loads(row["Record key"])
                if (isinstance(key, list) and len(key) == 2
                        and isinstance(key[0], str) and type(key[1]) is int):
                    copy["Record key"] = f"{key[0]} · Line {key[1]}"
            except (ValueError, TypeError):
                pass
        displayed.append(copy)
    return displayed


def _render_trace(record):
    st.caption("Factual provenance and check outcomes from the deterministic engine.")
    if record is None:
        st.info("Run a check to see the source records and decision outcome.")
        return
    outcome = record.get("action_result")
    if outcome:
        st.text("Human confirmation received; authoritative revalidation requested.")
        st.text(f"Simulation outcome: {outcome.status}")
        if outcome.action:
            st.text("Authoritative revalidation passed.")
            st.text(f"{outcome.action.action_id} · {outcome.action.status}")
    view = record["view"]
    if record.get("intent"):
        st.text(f"Parsed intent: {record['intent']}")
    if view is None:
        st.caption("General conversation does not invoke operational tools or produce enterprise evidence.")
        return
    st.text(f"Action: {view['action']}")
    st.text(f"Tool: {view['tool']}")
    if view.get("knowledge"):
        st.dataframe(view["knowledge"], hide_index=True)
    st.markdown("**Deterministic decision**")
    st.text(f"Outcome: {view['status']['label']}")
    st.caption(f"Evaluated: {record['time']} · Dataset: {record['fingerprint']}")
    st.markdown("**Tools / checks invoked**")
    st.dataframe(view["checks"], hide_index=True, width="stretch")
    st.markdown("**Source records**")
    if view["evidence"]:
        st.dataframe(_display_evidence(view["evidence"]), hide_index=True, width="stretch")
    else:
        st.caption("No source records returned. Missing records are not replaced with sample data.")
    if view["issues"]:
        st.markdown("**Issue codes**")
        st.text(", ".join(dict.fromkeys(i["Code"] for i in view["issues"])))


def _render_context_rail():
    """Helpful demo context only; it does not invoke application operations."""
    st.markdown('<aside class="db-support"><b>DealerBRAIN Tips</b><br><span class="db-small">Include a PO or part number.<br>Ask “why?” or “what next?”<br>Expand evidence to see the sources.</span></aside>', unsafe_allow_html=True)
    st.markdown('<aside class="db-support"><b>Demo & Trust</b><br><span class="db-small">Evidence-backed decisions.<br>Human-confirmed POC_SIMULATED actions.<br>No real system changes.</span></aside>', unsafe_allow_html=True)


def _render_vision_agent():
    """Photo-to-part vision agent. Description is interpretive; matches are deterministic."""
    with st.expander("📷 Identify a part from a photo · Vision Agent", expanded=False):
        st.caption(
            "Photograph a damaged or unlabeled part. The vision agent only describes what it "
            "sees — matching against the parts catalog is a deterministic lexical lookup, and "
            "no stock, warranty, or claim action runs until you pick a match and run a check."
        )
        if get_llm_status().state != "Configured":
            st.info("Photo identification requires configured AI. Guided checks below remain available.")
            return
        photo = st.file_uploader(
            "Upload a photo (PNG or JPEG, up to 5 MB)",
            type=["png", "jpg", "jpeg"],
            key="vision_photo",
        )
        if photo is not None and st.button("Identify part from photo", key="run_vision"):
            image_bytes = photo.getvalue()
            if len(image_bytes) > vision.MAX_IMAGE_BYTES:
                st.session_state["vision_result"] = None
                st.error("Photo exceeds the 5 MB limit. Use a smaller image.")
            else:
                with st.spinner("Analyzing photo…"):
                    st.session_state["vision_result"] = vision.identify_part_from_photo(
                        image_bytes, photo.type or "image/png", LLMClient(),
                    )
        result = st.session_state.get("vision_result")
        if result is None:
            return
        if result.status == "UNAVAILABLE":
            st.warning("Photo analysis is unavailable right now. Try again or use a guided check.")
            return
        if result.status == "INVALID_INPUT":
            st.error("This photo could not be read. Use a PNG, JPEG, or WEBP file under 5 MB.")
            return
        st.markdown(f"**Vision agent description:** {result.description}")
        st.caption("Interpretive only, not a confirmed catalog match — confirm a candidate below.")
        if not result.candidates:
            st.info("No catalog parts matched this description. Try Check Part or Check Stock below.")
            return
        st.dataframe(
            [
                {
                    "Part No": match.record.part_no,
                    "Part Name": match.record.part_name,
                    "Category": match.record.category,
                    "Status": match.record.status,
                    "Match score": match.score,
                }
                for match in result.candidates
            ],
            hide_index=True,
            width="stretch",
        )
        for match in result.candidates:
            st.button(
                f"Use {match.record.part_no} → Check Part",
                key=f"vision_use_{match.record.part_no}",
                on_click=_prefill,
                args=("Check Part", "part_no", match.record.part_no),
            )


def render_command_center(metadata):
    if st.session_state.get("dataset_sha") != metadata["sha256"]:
        st.session_state["history"] = []
        st.session_state["latest"] = None
        st.session_state["copilot_notice"] = None
        st.session_state["vision_result"] = None
        st.session_state["dataset_sha"] = metadata["sha256"]
    center, context = st.columns([0.8, 0.2], gap="large")
    with center:
        with st.container(key="quick_prompts"):
            for column, prompt in zip(st.columns(len(QUICK_PROMPTS), gap="small"), QUICK_PROMPTS):
                column.button(prompt, key=f"quick_{prompt}", on_click=_prefill_question,
                              args=(prompt,), width="stretch", help="Prefill this question, then send.")
        question = st.chat_input(
            "Message DealerBRAIN...",
            key="copilot_question", max_chars=4000,
        )
        if question:
            st.session_state["latest"] = None
            st.session_state["copilot_notice"] = None
            try:
                with st.spinner("Checking stock, rules and evidence…"):
                    interaction = run_copilot(
                        question, LLMClient(), st.session_state["history"][-1]
                        if st.session_state["history"] else None,
                    )
                if interaction["view"] is None and interaction.get("intent") != "GENERAL_CHAT":
                    st.session_state["copilot_notice"] = interaction["message"]
                else:
                    record = {
                        **interaction, "question": question, "inputs": [],
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
        response_area = st.container()
        _render_vision_agent()
        with st.expander("Guided checks · advanced", expanded=False):
            st.caption("Choose a guided check using operational records.")
            first, second = st.columns(2, gap="medium")
            first.button("Fulfillment demo", on_click=_prefill,
                         args=("Plan Fulfillment", "fulfillment_po", "PO-2026-1026"))
            second.button("Governance scenario", on_click=_prefill,
                          args=("Evaluate Purchase Order", "po_id", "PO-2026-1106"))
            st.caption("Shortcuts prefill only. Select Run check to plan; confirmation is a separate step.")
            st.divider()
            # Streamlit expanders cannot be nested (breaks appearance across screen sizes),
            # so these demo shortcut groups are plain labeled sections, not sub-expanders.
            st.markdown("**Knowledge guidance examples**")
            st.caption("Prefill a policy question, then send it in the chat box above.")
            for question in KNOWLEDGE_EXAMPLES:
                st.button(question, on_click=_prefill_question, args=(question,), width="stretch")
            st.divider()
            st.markdown("**Demo Scenarios**")
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
            st.divider()
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
                    result = function(po_id=inputs[0]) if action == "Plan Fulfillment" else function(*inputs)
                view = _execution_view(result, action, function.__name__,
                                       inputs[0] if action == "Plan Fulfillment" else None, check)
                record = {
                    "view": view,
                    "plan": result if isinstance(result, FulfillmentPlan) else None,
                    "po_id": inputs[0] if action == "Plan Fulfillment" else None,
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
        conversation = [record for record in st.session_state["history"] if record.get("question")]
        if not conversation and latest and latest.get("question"):
            conversation = [latest]
        if latest and not latest.get("question"):
            with response_area:
                with st.chat_message("assistant"):
                    _render_result(latest["view"])
                    _render_action(latest)
                    _render_details(latest["view"])
                    with st.expander("Evidence & Decision Trace", expanded=False):
                        _render_trace(latest)
        if conversation:
            with response_area:
                for record in reversed(conversation):
                    with st.chat_message("user"):
                        st.text(record["question"])
                    with st.chat_message("assistant"):
                        if record is latest and record.get("view"):
                            _render_result(record["view"])
                            _render_action(record)
                        st.text(record["message"])
                        if record is latest and record.get("view"):
                            _render_details(record["view"])
                        if record.get("view"):
                            with st.expander("Evidence & Decision Trace", expanded=False):
                                _render_trace(record)
        elif not latest:
            with response_area:
                st.info("Ask naturally — try a PO, part, dealer, claim, stock or policy question.")
    with context:
        _render_context_rail()
    with st.expander("Session History", expanded=False):
        history = st.session_state["history"]
        if history:
            st.dataframe(
                [
                    {
                        "Time (UTC)": r["time"],
                        "Action": r["view"]["action"] if r.get("view") else "General conversation",
                        "Inputs": ", ".join(str(v) for v in r["inputs"]),
                        "Status": r["view"]["status"]["label"] if r.get("view") else "N/A",
                        "Evidence records": len(r["view"]["evidence"]) if r.get("view") else 0,
                    }
                    for r in reversed(history)
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("Completed checks will appear here for this session only.")
        st.caption("Last 20 checks. History is not written to a database.")

    with st.expander("Recent POC simulated actions", expanded=False):
        try:
            recent = transactions.list_simulated_actions()
            if recent:
                st.dataframe(recent, hide_index=True)
            else:
                st.caption("No POC simulated actions recorded.")
        except (OSError, ValueError):
            st.info("Local simulation history is unavailable.")
