"""
sections/returns.py
-------------------
Placeholder for the "Mark Orders as Returned" section.
Functionality and final name will be decided later.
"""

import streamlit as st

from utils.ui import step_header


def render() -> None:
    step_header(1, "Coming soon", "This section is being designed.", state="locked")
    with st.container(border=True):
        st.markdown(
            """
            #### 🔄 Mark Self-Ship Orders as Returned

            This section will let the operations team mark Flipkart Self-Ship orders as
            **returned**. The exact workflow (inputs, checks, and Flipkart API calls) will be
            added in a later phase.

            Nothing here sends anything to Flipkart yet.
            """
        )
