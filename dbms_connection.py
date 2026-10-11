import os
import mysql.connector
from mysql.connector import Error

DB_NAME = os.getenv("SMARTSCHED_DB", "smartsched_ai")
DB_HOST = os.getenv("SMARTSCHED_DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("SMARTSCHED_DB_PORT", "3306"))
DB_USER = os.getenv("SMARTSCHED_DB_USER", "root")
DB_PASSWORD = os.getenv("SMARTSCHED_DB_PASSWORD", "arjun@0516")


def get_connection():
    """Connect to the SmartSched MySQL database."""
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        connection_timeout=5,
    )


def get_database_status():
    """Return database connection, table and row-count information."""
    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute("SELECT DATABASE(), VERSION()")
        database_name, version = cursor.fetchone()

        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]

        expected_tables = [
            "processes",
            "ml_burst_predictions",
            "process_transaction_logs",
            "system_savepoints",
            "system_telemetry_audit",
        ]

        counts = {}
        for table in expected_tables:
            if table in tables:
                # Table names come from the database's SHOW TABLES result.
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                counts[table] = cursor.fetchone()[0]

        cursor.close()

        return {
            "connected": True,
            "database": database_name,
            "mysql_version": version,
            "tables": tables,
            "missing_tables": [
                table for table in expected_tables
                if table not in tables
            ],
            "counts": counts,
            "error": None,
        }

    except Error as exc:
        return {
            "connected": False,
            "database": DB_NAME,
            "mysql_version": None,
            "tables": [],
            "missing_tables": [],
            "counts": {},
            "error": str(exc),
        }

    finally:
        if connection is not None and connection.is_connected():
            connection.close()
