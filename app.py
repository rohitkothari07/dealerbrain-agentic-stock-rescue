"""Minimal Streamlit entry point for the DealerBRAIN foundation."""

import streamlit as st

from config import APP_NAME, APP_TAGLINE, TEAM_NAME
from data_loader import DataInitializationError, initialize_data


def main() -> None:
    """Display branding and the current foundation status."""
    st.set_page_config(page_title=APP_NAME, layout="centered")
    st.title(APP_NAME)
    st.write(APP_TAGLINE)
    st.caption(f"Team {TEAM_NAME}")

    st.header("POC Environment")
    st.write("✓ Application initialized")
    metadata = None
    try:
        metadata = initialize_data()
        st.write("✓ Data layer: ready")
    except DataInitializationError as exc:
        st.write("✗ Data layer: failed")
        st.error(str(exc))
    st.write("○ AI layer: pending")
    st.write("○ Workflow layer: pending")

    if metadata is not None:
        issues = metadata["issues"]
        with st.expander("Dataset Health", expanded=False):
            st.write(f"Source filename: {metadata['source_filename']}")
            st.write(f"Dataset fingerprint: {metadata['sha256'][:16]}…")
            st.write(f"Operational table count: {len(metadata['tables'])}")
            st.write(f"Total operational rows: {sum(t['rows'] for t in metadata['tables'])}")
            st.write(f"Validation error count: {sum(i['severity'] == 'ERROR' for i in issues)}")
            st.write(f"Validation warning count: {sum(i['severity'] == 'WARNING' for i in issues)}")
            st.dataframe([
                {"Table": table["table"], "Rows": table["rows"],
                 "Columns": len(table["columns"]),
                 "Status": "Warnings" if any(
                     i["table"] == table["table"] and i["severity"] == "WARNING"
                     for i in issues) else "Ready"}
                for table in metadata["tables"]
            ], hide_index=True)


if __name__ == "__main__":
    main()
