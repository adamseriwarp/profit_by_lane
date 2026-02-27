import pymysql
import os
from dotenv import load_dotenv
import streamlit as st
import pandas as pd

# Load environment variables (for local development)
load_dotenv()

def get_secret(key, default=None):
    """
    Get a secret from Streamlit Cloud secrets or environment variables.
    Streamlit Cloud uses st.secrets, local dev uses .env
    """
    # Try Streamlit secrets first (for cloud deployment)
    try:
        if hasattr(st, 'secrets') and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass

    # Fall back to environment variables (for local development)
    return os.getenv(key, default)

@st.cache_resource
def get_db_connection():
    """
    Create and return a MySQL database connection.
    Uses st.cache_resource to maintain a single connection across reruns.
    """
    try:
        connection = pymysql.connect(
            host=get_secret('DB_HOST'),
            port=int(get_secret('DB_PORT', 3306)),
            user=get_secret('DB_USER'),
            password=get_secret('DB_PASSWORD'),
            database=get_secret('DB_NAME'),
            cursorclass=pymysql.cursors.DictCursor
        )
        return connection
    except Exception as e:
        st.error(f"Error connecting to MySQL database: {e}")
        return None

def execute_query(query, params=None):
    """
    Execute a SQL query and return results as a pandas DataFrame.

    Args:
        query (str): SQL query to execute
        params (tuple, optional): Parameters for parameterized queries

    Returns:
        pandas.DataFrame: Query results
    """
    connection = get_db_connection()

    if connection is None:
        st.error("Error executing query: MySQL Connection not available")
        return None

    try:
        # Check if connection is still alive, reconnect if needed
        connection.ping(reconnect=True)

        cursor = connection.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        results = cursor.fetchall()
        cursor.close()

        return pd.DataFrame(results)

    except Exception as e:
        # Clear the cached connection so it can be recreated
        get_db_connection.clear()
        st.error(f"Error executing query: {e}")
        return None

def test_connection():
    """
    Test the database connection and return connection status.

    Returns:
        bool: True if connection successful, False otherwise
    """
    connection = get_db_connection()

    if connection:
        try:
            connection.ping(reconnect=True)
            return True, "Successfully connected to MySQL Server"
        except:
            return False, "Failed to connect to database"
    else:
        return False, "Failed to connect to database"

