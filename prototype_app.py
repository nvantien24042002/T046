"""Separate Streamlit prototype; app.py and its workflows remain untouched."""
import os

import streamlit as st

from ui.upload_component import upload_test_zip

st.set_page_config(page_title="CVAT upload prototype")
st.title("Small ZIP upload prototype")
st.warning("16 MiB test limit. No durable resume, restart recovery, ZIP validation or dataset registration yet.")
key = os.environ.get("CVAT_PROTOTYPE_KEY", "")
if len(key) < 32:
    st.error("Configure a random CVAT_PROTOTYPE_KEY for both local processes before starting.")
    st.stop()

st.button("Rerun page", key="rerun-prototype")
result = upload_test_zip("http://127.0.0.1:8010", key)
if result.result:
    st.json(result.result)
