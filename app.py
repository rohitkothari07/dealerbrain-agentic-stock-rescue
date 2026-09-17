"""DealerBRAIN command center for deterministic checks and operational evidence."""

import streamlit as st

from config import APP_NAME
from data_loader import DataInitializationError, initialize_data
from llm_client import get_llm_status
from ui import (
    ACTIONS, render_command_center, render_dataset_health, render_control_tower, render_demo_shell,
    render_engagement_popup, render_inventory_dashboard, render_theme_toggle,
)


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🤖", layout="wide", initial_sidebar_state="expanded")
    st.session_state.setdefault("ui_theme", "light")
    render_engagement_popup()
    title_col, toggle_col = st.columns([0.85, 0.15])
    with title_col:
        st.title(APP_NAME)
        st.markdown("After-Sales Stock Rescue Copilot")
        st.caption("Grounded decisions. Visible evidence. Human approval.")
    with toggle_col:
        render_theme_toggle()
    llm_status = get_llm_status()
    try:
        metadata = initialize_data()
    except DataInitializationError:
        st.error("Operational data is unavailable. Check the supplied workbook and data setup.")
        st.caption("DealerBRAIN is not ready to evaluate requests.")
        return
    render_demo_shell(metadata, llm_status)
    render_inventory_dashboard()
    st.divider()
    render_command_center(metadata)
    with st.expander("System diagnostics", expanded=False):
        st.caption(f"LLM Adapter: {llm_status.state} · {llm_status.detail}")
        render_control_tower(metadata)
        render_dataset_health(metadata)
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
