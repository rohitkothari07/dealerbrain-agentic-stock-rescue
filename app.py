"""DealerBRAIN command center for deterministic checks and operational evidence."""

import streamlit as st

from config import APP_NAME, APP_TAGLINE, TEAM_NAME
from data_loader import DataInitializationError, initialize_data
from llm_client import get_llm_status
from ui import ACTIONS, render_command_center, render_dataset_health


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🧠", layout="wide")
    st.title(APP_NAME)
    st.write(APP_TAGLINE)
    st.caption(f"Team {TEAM_NAME}")
    llm_status = get_llm_status()
    try:
        metadata = initialize_data()
    except DataInitializationError as exc:
        st.error(f"Data: Failed — {exc}")
        st.caption("Decision Engine: Unavailable · AI Orchestrator: Pending")
        return
    ready = all(callable(action[0]) for action in ACTIONS.values())
    st.caption(
        f"Data: Ready · Decision Engine: {'Ready' if ready else 'Unavailable'} · "
        "AI Orchestrator: Pending"
    )
    st.caption("Deterministic tools · Read-only business checks · AI orchestration is not enabled")
    st.caption(f"LLM Adapter: {llm_status.state} · {llm_status.detail}")
    st.divider()
    render_command_center(metadata)
    st.divider()
    render_dataset_health(metadata)
    with st.expander("Business Rules Health", expanded=False):
        st.caption(
            "Tool availability after data initialization; not an external service health probe."
        )
        st.dataframe(
            [
                {
                    "Capability": name,
                    "Availability": "Ready" if callable(action[0]) else "Unavailable",
                }
                for name, action in ACTIONS.items()
            ],
            hide_index=True,
        )


if __name__ == "__main__":
    main()
