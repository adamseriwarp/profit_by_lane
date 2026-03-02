import streamlit as st

def check_password():
    """Returns `True` if the user has entered the correct password."""

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        # Use .get() to safely access the password key (may not exist on page refresh)
        password = st.session_state.get("password", "")
        # Only validate if password field has a value (user actually submitted)
        if password:
            if password == st.secrets.get("APP_PASSWORD", ""):
                st.session_state["password_correct"] = True
                del st.session_state["password"]  # Don't store password
            else:
                st.session_state["password_correct"] = False

    # If already authenticated, stay authenticated
    if st.session_state.get("password_correct", False):
        return True

    if "password_correct" not in st.session_state:
        # First run, show input for password
        st.text_input(
            "Password", type="password", on_change=password_entered, key="password"
        )
        return False
    elif not st.session_state["password_correct"]:
        # Password incorrect, show input + error
        st.text_input(
            "Password", type="password", on_change=password_entered, key="password"
        )
        st.error("😕 Password incorrect")
        return False
    else:
        # Password correct
        return True

