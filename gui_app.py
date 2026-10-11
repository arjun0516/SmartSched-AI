
import json
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import mysql.connector
from mysql.connector import Error
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from dbms_connection import get_database_status

try:
    import ml_model
except ImportError:
    ml_model = None


BASE_DIR = Path(__file__).resolve().parent
DB_NAME = os.getenv("SMARTSCHED_DB", "smartsched_ai")
DB_HOST = os.getenv("SMARTSCHED_DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("SMARTSCHED_DB_PORT", "3306"))
DB_USER = os.getenv("SMARTSCHED_DB_USER", "root")
DB_PASSWORD = os.getenv("SMARTSCHED_DB_PASSWORD", "arjun@0516")

# Set this environment variable to the full path of your compiled
# C++ executable. Example: C:\Users\Acer\SmartSched-AI\scheduler.exe
SCHEDULER_EXE = os.getenv(
    "SMARTSCHED_SCHEDULER_EXE",
    str(BASE_DIR / "scheduler.exe"),
)

APP = FastAPI(title="SmartSched AI OS Platform", version="5.0")
app = APP

_lock = threading.Lock()
recent_results = []
MAX_RESULTS = 200


ROLLBACK_RULES = [
    {
        "id": 1,
        "name": "Memory Capacity Exceeded",
        "code": "CONSISTENCY_VIOLATION",
        "why": "Requested RAM exceeds the 2048 MB limit.",
    },
    {
        "id": 2,
        "name": "Priority Boundary Exceeded",
        "code": "CONSTRAINT_VIOLATION",
        "why": "Priority must be between 1 and 5.",
    },
    {
        "id": 3,
        "name": "Execution Timeout Exceeded",
        "code": "TIMEOUT_EXCEEDED",
        "why": "Actual execution time exceeds the maximum burst time.",
    },
    {
        "id": 4,
        "name": "Deadlock / Wait Time Exceeded",
        "code": "DEADLOCK_ABORT",
        "why": "The process was marked for deadlock abort.",
    },
    {
        "id": 5,
        "name": "Disk Storage Exceeded",
        "code": "QUOTA_VIOLATION",
        "why": "Disk usage exceeds the configured disk quota.",
    },
    {
        "id": 6,
        "name": "Simulated Runtime Exception",
        "code": "RUNTIME_FAULT",
        "why": "A simulated runtime fault was requested.",
    },
]


def db_connection():
    """Connect to the MySQL database created by the supplied SQL script."""
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        connection_timeout=5,
    )


def get_rows(sql, params=None):
    conn = db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params or ())
        return cursor.fetchall()
    finally:
        conn.close()


def get_one(sql, params=None):
    rows = get_rows(sql, params)
    return rows[0] if rows else {}


def save_transaction(request_data, result):
    """
    Save both successful and rolled-back attempts using the supplied
    process_transaction_logs table.

    A rollback attempt is logged without inserting an invalid process
    into the constrained processes table.
    """
    conn = db_connection()
    try:
        cursor = conn.cursor()
        outcome = (
            "COMMIT"
            if result.get("status") == "COMMITTED"
            else "ROLLBACK"
        )
        error_code = result.get("error_code") or None
        message = (
            result.get("dbms_message")
            or result.get("rollback_reason")
            or "Committed Successfully"
        )

        cursor.execute(
            """
            INSERT INTO process_transaction_logs
                (pid, outcome, error_code, message,
                 request_json, result_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                int(request_data["pid"]),
                outcome,
                error_code,
                str(message)[:255],
                json.dumps(request_data),
                json.dumps(result),
            ),
        )

        if outcome == "ROLLBACK":
            cursor.execute(
                """
                INSERT INTO system_telemetry_audit (event_type, details)
                VALUES (%s, %s)
                """,
                (
                    "ROLLBACK",
                    json.dumps({
                        "pid": request_data["pid"],
                        "error_code": error_code,
                        "message": message,
                        "rollback_condition": result.get(
                            "rollback_condition", ""
                        ),
                    }),
                ),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remember_result(result):
    result["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        recent_results.insert(0, result)
        del recent_results[MAX_RESULTS:]


def run_cpp_scheduler(payload):
    executable = Path(SCHEDULER_EXE)

    if not executable.exists():
        raise FileNotFoundError(
            "C++ scheduler executable not found. Compile your C++ file "
            "to scheduler.exe or set SMARTSCHED_SCHEDULER_EXE."
        )

    completed = subprocess.run(
        [str(executable)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(BASE_DIR),
        check=False,
    )

    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip()
            or f"C++ scheduler exited with code {completed.returncode}."
        )

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "C++ scheduler returned invalid JSON: "
            + completed.stdout[:300]
        ) from exc

    return result


def fallback_scheduler(payload):
    """
    Python fallback so the dashboard can still be tested before the
    C++ executable is compiled. This is a simplified single-process
    simulation, not a replacement for the C++ scheduling algorithms.
    """
    pid = int(payload["pid"])
    ram = int(payload["ram_mb"])
    priority = int(payload["priority"])
    burst = int(payload["max_burst_ms"])
    actual = int(payload["actual_ms"])
    disk = int(payload["disk_mb"])
    quota = int(payload["disk_quota_mb"])

    condition = None

    if ram > 2048:
        condition = ROLLBACK_RULES[0]
    elif priority < 1 or priority > 5:
        condition = ROLLBACK_RULES[1]
    elif actual > burst:
        condition = ROLLBACK_RULES[2]
    elif payload.get("simulate_deadlock"):
        condition = ROLLBACK_RULES[3]
    elif disk > quota:
        condition = ROLLBACK_RULES[4]
    elif payload.get("inject_fault"):
        condition = ROLLBACK_RULES[5]

    predicted = max(1.0, burst * 0.85)
    waiting = int(payload["arrival_ms"]) + max(0, priority - 1) * 5
    temperature = round(45 + min(60, actual / 2), 1)

    if condition:
        return {
            "ok": False,
            "pid": pid,
            "status": "ROLLED_BACK",
            "acid_status": "ROLLED_BACK",
            "rollback_condition": condition["name"],
            "error_code": condition["code"],
            "dbms_message": condition["why"],
            "algorithm": payload["algorithm"],
            "predicted_ms": predicted,
            "actual_ms": actual,
            "error_ms": abs(predicted - actual),
            "waiting_ms": waiting,
            "turnaround_ms": waiting + actual,
            "estimated_temperature_c": temperature,
            "simulated_power_watts": round(12 + actual * 0.2, 1),
        }

    if payload.get("simulate_thermal") and temperature > 85:
        return {
            "ok": False,
            "pid": pid,
            "status": "ROLLED_BACK",
            "acid_status": "ROLLED_BACK",
            "rollback_condition": "Thermal Throttling",
            "error_code": "THERMAL_THROTTLE",
            "dbms_message": (
                "Simulated core temperature exceeded 85 C."
            ),
            "algorithm": payload["algorithm"],
            "predicted_ms": predicted,
            "actual_ms": actual,
            "error_ms": abs(predicted - actual),
            "waiting_ms": waiting,
            "turnaround_ms": waiting + actual,
            "estimated_temperature_c": temperature,
            "simulated_power_watts": round(12 + actual * 0.2, 1),
        }

    return {
        "ok": True,
        "pid": pid,
        "core": 1,
        "status": "COMMITTED",
        "acid_status": "COMMITTED",
        "dbms_message": "Committed Successfully",
        "ram_mb": ram,
        "disk_mb": disk,
        "priority": priority,
        "predicted_ms": predicted,
        "actual_ms": actual,
        "error_ms": abs(predicted - actual),
        "waiting_ms": waiting,
        "turnaround_ms": waiting + actual,
        "algorithm": payload["algorithm"],
        "queue": "Q0 - Interactive" if priority == 1 else (
            "Q1 - Standard" if priority <= 3 else "Q2 - Batch"
        ),
        "quantum_ms": 10 if priority == 1 else (
            20 if priority <= 3 else 40
        ),
        "estimated_temperature_c": temperature,
        "simulated_power_watts": round(12 + actual * 0.2, 1),
    }


def dispatch(payload):
    # Map the I/O field name expected by the C++ scheduler.
    if "io_ratio_pct" in payload:
        payload["io_ratio"] = payload["io_ratio_pct"]

    # Prefer the real C++ scheduler. Use the Python fallback only when
    # the executable has not yet been compiled.
    try:
        result = run_cpp_scheduler(payload)
    except FileNotFoundError:
        result = fallback_scheduler(payload)

    # Standardize the status and message fields.
    if not result.get("status"):
        result["status"] = (
            "COMMITTED" if result.get("ok") else "ROLLED_BACK"
        )

    if result["status"] == "ROLLED_BACK":
        result["rollback_reason"] = (
            result.get("dbms_message")
            or result.get("rollback_condition")
            or "Rollback reason was not returned by the scheduler."
        )

    # Add ML prediction information if the supplied ML module is available.
    if ml_model is not None:
        try:
            features = {
                "ram_mb": float(payload["ram_mb"]),
                "priority": float(payload["priority"]),
                "io_ratio_pct": float(payload["io_ratio"]),
                "arrival_ms": float(payload["arrival_ms"]),
            }
            prediction, model_name = ml_model.predict(features)
            result["ml_predicted_ms"] = round(float(prediction), 2)
            result["ml_model"] = model_name

            if result["status"] == "COMMITTED":
                ml_model.record(features, payload["actual_ms"])
        except Exception as exc:
            result["ml_warning"] = str(exc)

    # Persist the transaction if MySQL is available. A database failure
    # is reported instead of pretending that the record was saved.
    try:
        save_transaction(payload, result)
        result["database_saved"] = True
    except Exception as exc:
        result["database_saved"] = False
        result["database_error"] = str(exc)

    remember_result(result)
    return result


def safe_text(value):
    if value is None:
        return ""
    value = str(value)
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def page(content, title="SmartSched AI"):
    return HTMLResponse(f"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_text(title)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root {{
  --bg:#f3f6fb; --panel:#fff; --ink:#172033; --muted:#65718a;
  --line:#e3e8f1; --nav:#17233c; --accent:#425ee8;
  --green:#16794b; --red:#b42332; --amber:#946200;
}}
* {{box-sizing:border-box}}
body {{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 "Segoe UI",Arial,sans-serif}}
header {{background:var(--nav);color:#fff;padding:19px 24px}}
header h1 {{font-size:23px;margin:0}}
header p {{margin:4px 0 0;color:#cbd5eb}}
nav {{display:flex;flex-wrap:wrap;gap:8px;padding:12px 20px;
  background:#fff;border-bottom:1px solid var(--line)}}
nav a {{text-decoration:none;color:#34415c;padding:9px 12px;
  border-radius:7px;font-weight:600}}
nav a:hover {{background:#eaf0ff;color:var(--accent)}}
main {{max-width:1500px;margin:auto;padding:22px}}
h2 {{font-size:19px;margin:0 0 14px}}
h3 {{font-size:15px;margin:0 0 10px}}
.grid {{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));
  gap:14px;margin-bottom:18px}}
.two {{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
  gap:16px;margin-bottom:18px}}
.panel,.metric {{background:var(--panel);border:1px solid var(--line);
  border-radius:12px;padding:17px;min-width:0}}
.metric .label {{color:var(--muted);font-size:12px}}
.metric .value {{font-size:25px;font-weight:750;margin-top:5px}}
.muted {{color:var(--muted)}}
input,select {{width:100%;padding:10px;border:1px solid #cfd7e6;
  border-radius:7px;background:#fff;color:var(--ink)}}
label {{display:block;font-weight:600;margin-bottom:5px}}
.field {{margin-bottom:12px}}
button,.button {{display:inline-block;background:var(--accent);
  color:white;border:0;border-radius:7px;padding:10px 14px;
  font-weight:650;cursor:pointer;text-decoration:none}}
button:hover,.button:hover {{filter:brightness(.94)}}
table {{width:100%;border-collapse:collapse;min-width:650px}}
th,td {{text-align:left;padding:10px;border-bottom:1px solid var(--line);
  vertical-align:top}}
th {{background:#f7f9fd;color:#4d5870;font-size:12px}}
.table-wrap {{overflow-x:auto}}
.status {{font-weight:750}}
.good {{color:var(--green)}}
.bad {{color:var(--red)}}
.warn {{color:var(--amber)}}
.note {{padding:12px;border-radius:8px;background:#f0f4ff;
  margin:12px 0;overflow-wrap:anywhere}}
.error {{background:#fff0f0;border:1px solid #f2c4c4;color:#8e2020}}
.result {{margin-top:15px}}
small {{color:var(--muted)}}
.check {{display:flex;gap:9px;align-items:center;margin:10px 0}}
.check input {{width:auto}}
@media(max-width:900px) {{
  .grid {{grid-template-columns:repeat(2,minmax(0,1fr))}}
  .two {{grid-template-columns:1fr}}
}}
@media(max-width:540px) {{
  main {{padding:12px}}
  .grid {{grid-template-columns:1fr 1fr;gap:8px}}
  .metric {{padding:12px}}
  .metric .value {{font-size:20px}}
}}
</style>
</head>
<body>
<header>
  <h1>SmartSched AI</h1>
  <p>Process Scheduling · ACID Transaction Logging · ML Burst Prediction</p>
</header>
<nav>
  <a href="/">Dashboard</a>
  <a href="/dispatcher">Process Dispatcher</a>
  <a href="/rollbacks">Rollback Rules</a>
  <a href="/transactions">Transaction History</a>
  <a href="/analytics">Analytics &amp; ML</a>
  <a href="/logs">System Logs</a>
  <a href="/dbms-monitor">DBMS Monitor</a>
</nav>
<main>{content}</main>
</body>
</html>
""")


def table(headers, rows):
    head = "".join(f"<th>{safe_text(h)}</th>" for h in headers)
    body = ""
    for row in rows:
        body += "<tr>" + "".join(
            f"<td>{safe_text(value)}</td>" for value in row
        ) + "</tr>"
    if not rows:
        body = (
            f'<tr><td colspan="{len(headers)}" class="muted">'
            "No records available yet.</td></tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table></div>"
    )


def read_transactions(limit=100):
    try:
        return get_rows(
            """
            SELECT id, pid, outcome, error_code, message, created_at
            FROM process_transaction_logs
            ORDER BY id DESC LIMIT %s
            """,
            (limit,),
        )
    except Exception:
        return []


def read_processes(limit=100):
    try:
        return get_rows(
            """
            SELECT pid, assigned_core, ram_mb, priority,
                   predicted_burst_ms, actual_burst_ms,
                   waiting_time_ms, status, created_at
            FROM processes
            ORDER BY id DESC LIMIT %s
            """,
            (limit,),
        )
    except Exception:
        return []


def result_rows():
    with _lock:
        return list(recent_results)


@APP.get("/", response_class=HTMLResponse)
def dashboard():
    processes = read_processes()
    transactions = read_transactions(10)
    results = result_rows()

    committed = sum(
        1 for t in transactions if t.get("outcome") == "COMMIT"
    )
    rolled_back = sum(
        1 for t in transactions if t.get("outcome") == "ROLLBACK"
    )
    avg_wait = (
        sum(float(p.get("waiting_time_ms") or 0) for p in processes)
        / len(processes)
        if processes else 0
    )

    metrics_html = f"""
    <div class="grid">
      <div class="metric"><div class="label">Saved processes</div>
        <div class="value">{len(processes)}</div></div>
      <div class="metric"><div class="label">Committed transactions</div>
        <div class="value good">{committed}</div></div>
      <div class="metric"><div class="label">Rollbacks logged</div>
        <div class="value bad">{rolled_back}</div></div>
      <div class="metric"><div class="label">Average waiting time</div>
        <div class="value">{avg_wait:.2f} ms</div></div>
    </div>
    """

    proc_rows = [
        [
            p.get("pid"), p.get("assigned_core"), p.get("ram_mb"),
            p.get("priority"), p.get("predicted_burst_ms"),
            p.get("actual_burst_ms"), p.get("waiting_time_ms"),
            p.get("status"), p.get("created_at"),
        ]
        for p in processes
    ]

    recent_html = ""
    if results:
        recent = results[:8]
        recent_html = table(
            ["PID", "Status", "Algorithm", "Predicted ms",
             "Actual ms", "Rollback reason"],
            [
                [
                    r.get("pid"), r.get("status"), r.get("algorithm"),
                    r.get("predicted_ms"), r.get("actual_ms"),
                    r.get("rollback_reason", ""),
                ]
                for r in recent
            ],
        )

    content = f"""
    <h2>Dashboard</h2>
    {metrics_html}
    <div class="two">
      <section class="panel">
        <h3>Process status</h3>
        <canvas id="statusChart"></canvas>
      </section>
      <section class="panel">
        <h3>Transaction outcomes</h3>
        <canvas id="transactionChart"></canvas>
      </section>
    </div>
    <section class="panel" style="margin-bottom:18px">
      <h3>Master process table</h3>
      {table(
        ["PID", "Core", "RAM MB", "Priority", "Predicted ms",
         "Actual ms", "Waiting ms", "Status", "Created"],
        proc_rows,
      )}
    </section>
    <section class="panel">
      <h3>Recent dispatch results (this server session)</h3>
      {recent_html or '<p class="muted">Dispatch a process to see its result.</p>'}
    </section>
    <script>
    fetch('/api/analytics').then(r=>r.json()).then(d=>{{
      new Chart(document.getElementById('statusChart'), {{
        type:'doughnut',
        data:{{labels:['Committed','Rolled back'],datasets:[{{
          data:[d.committed||0,d.rolled_back||0]}}]}}
      }});
      new Chart(document.getElementById('transactionChart'), {{
        type:'bar',
        data:{{labels:['Commit','Rollback'],datasets:[{{
          label:'Transactions',
          data:[d.committed||0,d.rolled_back||0]}}]}}
      }});
    }});
    </script>
    """
    return page(content, "Dashboard | SmartSched AI")


@APP.get("/dispatcher", response_class=HTMLResponse)
def dispatcher():
    content = """
    <h2>Process Dispatcher</h2>
    <p class="muted">Submit one process and see the scheduler's decision
    and rollback explanation.</p>
    <section class="panel">
      <form method="post" action="/dispatch">
        <div class="grid">
          <div class="field"><label>Process ID (PID)</label>
            <input type="number" name="pid" value="1001" min="1" required>
          </div>
          <div class="field"><label>Arrival time (ms)</label>
            <input type="number" name="arrival_ms" value="0" min="0" required>
          </div>
          <div class="field"><label>RAM (MB)</label>
            <input type="number" name="ram_mb" value="512" min="1" required>
          </div>
          <div class="field"><label>Priority (1-5)</label>
            <input type="number" name="priority" value="2" required>
          </div>
          <div class="field"><label>Maximum burst (ms)</label>
            <input type="number" name="max_burst_ms" value="1000" min="1" required>
          </div>
          <div class="field"><label>Actual runtime (ms)</label>
            <input type="number" name="actual_ms" value="750" min="1" required>
          </div>
          <div class="field"><label>I/O ratio (0-100)</label>
            <input type="number" name="io_ratio" value="20" min="0" max="100" required>
          </div>
          <div class="field"><label>Number of cores (1-4)</label>
            <input type="number" name="cores" value="4" min="1" max="4" required>
          </div>
          <div class="field"><label>Disk usage (MB)</label>
            <input type="number" name="disk_mb" value="100" min="0" required>
          </div>
          <div class="field"><label>Disk quota (MB)</label>
            <input type="number" name="disk_quota_mb" value="1000" min="1" required>
          </div>
          <div class="field"><label>Maximum wait (ms)</label>
            <input type="number" name="max_wait_ms" value="2000" min="1" required>
          </div>
          <div class="field"><label>Scheduling algorithm</label>
            <select name="algorithm">
              <option>MLFQ</option>
              <option>FCFS</option>
              <option>SJF</option>
              <option>Priority</option>
              <option>Round Robin</option>
            </select>
          </div>
        </div>

        <h3>Optional test conditions</h3>
        <p class="muted">Select a condition only when you want to test it.
        If multiple rollback conditions apply, the first matching rule in
        the backend takes precedence.</p>

        <div class="field">
          <label>Force Rollback Test Condition (Optional)</label>
          <select name="rollback_test_condition">
            <option value="">None (Normal Execution)</option>
            <option value="1">Rule 1: Memory Capacity Exceeded (RAM > 2048 MB)</option>
            <option value="2">Rule 2: Priority Boundary Exceeded (Priority outside 1–5)</option>
            <option value="3">Rule 3: Execution Timeout Exceeded (Actual burst > Max burst)</option>
            <option value="4">Rule 4: Deadlock / Wait Time Exceeded</option>
            <option value="5">Rule 5: Disk Storage Exceeded (Disk usage > Quota)</option>
            <option value="6">Rule 6: Simulated Runtime Exception</option>
          </select>
        </div>

        <button type="submit">Dispatch Process</button>
      </form>
    </section>
    <div class="note">
      <strong>How to test the six rollback rules:</strong>
      use RAM above 2048 MB for rule 1; priority outside 1-5 for rule 2;
      actual runtime above maximum burst for rule 3; select deadlock for
      rule 4; disk usage above quota for rule 5; select runtime fault for
      rule 6. Each submission tests one process at a time.
    </div>
    """
    return page(content, "Process Dispatcher | SmartSched AI")


@APP.post("/dispatch")
def dispatch_form(
    pid: int = Form(...),
    arrival_ms: int = Form(0),
    ram_mb: int = Form(512),
    priority: int = Form(2),
    max_burst_ms: int = Form(1000),
    actual_ms: int = Form(750),
    io_ratio: int = Form(20),
    cores: int = Form(4),
    disk_mb: int = Form(100),
    disk_quota_mb: int = Form(1000),
    max_wait_ms: int = Form(2000),
    algorithm: str = Form("MLFQ"),
    simulate_deadlock: str = Form(""),
    inject_fault: str = Form(""),
    simulate_thermal: str = Form(""),
):
    allowed_algorithms = {
        "MLFQ", "FCFS", "SJF", "Priority", "Round Robin"
    }
    if algorithm not in allowed_algorithms:
        algorithm = "MLFQ"

    payload = {
        "pid": pid,
        "arrival_ms": arrival_ms,
        "ram_mb": ram_mb,
        "disk_mb": disk_mb,
        "disk_quota_mb": disk_quota_mb,
        "priority": priority,
        "max_burst_ms": max_burst_ms,
        "actual_ms": actual_ms,
        "io_ratio": io_ratio,
        "max_wait_ms": max_wait_ms,
        "cores": cores,
        "algorithm": algorithm,
        "simulate_deadlock": bool(simulate_deadlock),
        "inject_fault": bool(inject_fault),
        "simulate_thermal": bool(simulate_thermal),
    }

    try:
        result = dispatch(payload)
    except Exception as exc:
        result = {
            "ok": False,
            "status": "ERROR",
            "pid": pid,
            "dbms_message": str(exc),
        }

    status = result.get("status", "ERROR")
    rolled_back = status == "ROLLED_BACK"
    reason = (
        result.get("rollback_reason")
        or result.get("dbms_message")
        or "No rollback reason was returned."
    )

    fields = [
        ("Process ID", result.get("pid", pid)),
        ("Status", status),
        ("ACID status", result.get("acid_status", status)),
        ("Rollback condition", result.get("rollback_condition", "—")),
        ("Error code", result.get("error_code", "—")),
        ("Reason", reason if rolled_back else result.get(
            "dbms_message", "—"
        )),
        ("Assigned core", result.get("core", "—")),
        ("Algorithm", result.get("algorithm", algorithm)),
        ("Predicted runtime (ms)", result.get("predicted_ms", "—")),
        ("ML predicted runtime (ms)", result.get("ml_predicted_ms", "—")),
        ("Actual runtime (ms)", result.get("actual_ms", actual_ms)),
        ("Prediction error (ms)", result.get("error_ms", "—")),
        ("Waiting time (ms)", result.get("waiting_ms", "—")),
        ("Turnaround time (ms)", result.get("turnaround_ms", "—")),
        ("Queue", result.get("queue", "—")),
        ("Quantum (ms)", result.get("quantum_ms", "—")),
        ("Estimated temperature (C)", result.get(
            "estimated_temperature_c", "—"
        )),
        ("Simulated power (W)", result.get("simulated_power_watts", "—")),
        ("Saved to MySQL", result.get("database_saved", False)),
    ]

    if result.get("database_error"):
        fields.append(("Database error", result["database_error"]))
    if result.get("ml_warning"):
        fields.append(("ML warning", result["ml_warning"]))

    result_html = table(["Field", "Result"], fields)
    css_class = "error" if rolled_back or status == "ERROR" else ""

    content = f"""
    <h2>Dispatch Result</h2>
    <section class="panel result">
      <div class="note {css_class}">
        <strong>{safe_text(status)}</strong><br>
        {safe_text(reason)}
      </div>
      {result_html}
      <p style="margin-top:18px">
        <a class="button" href="/dispatcher">Dispatch another process</a>
        <a class="button" href="/transactions">View transaction history</a>
      </p>
    </section>
    """
    return page(content, "Dispatch Result | SmartSched AI")


@APP.get("/rollbacks", response_class=HTMLResponse)
def rollbacks():
    rows = [
        [
            r["id"], r["name"], r["code"], r["why"]
        ]
        for r in ROLLBACK_RULES
    ]

    recent_rollbacks = []
    for tx in read_transactions(100):
        if tx.get("outcome") == "ROLLBACK":
            recent_rollbacks.append([
                tx.get("pid"),
                tx.get("error_code"),
                tx.get("message"),
                tx.get("created_at"),
            ])

    content = f"""
    <h2>Rollback Rules</h2>
    <p class="muted">These are backend validation rules. The GUI displays
    the actual rule and message returned for each failed process.</p>
    <section class="panel" style="margin-bottom:18px">
      <h3>Six rollback conditions</h3>
      {table(["No.", "Condition", "Error code", "Why it happens"], rows)}
    </section>
    <section class="panel">
      <h3>Actual rollback history</h3>
      {table(["PID", "Error code", "Why rollback happened", "Time"],
             recent_rollbacks)}
    </section>
    """
    return page(content, "Rollback Rules | SmartSched AI")


@APP.get("/transactions", response_class=HTMLResponse)
def transactions():
    tx = read_transactions(200)
    rows = [
        [
            t.get("id"), t.get("pid"), t.get("outcome"),
            t.get("error_code") or "—", t.get("message"),
            t.get("created_at"),
        ]
        for t in tx
    ]

    content = f"""
    <h2>Transaction History</h2>
    <section class="panel">
      <p class="muted">Records are loaded from MySQL
      <code>process_transaction_logs</code>.</p>
      {table(["Transaction ID", "PID", "Outcome", "Error code",
              "Message / rollback reason", "Time"], rows)}
    </section>
    """
    return page(content, "Transaction History | SmartSched AI")


@APP.get("/analytics", response_class=HTMLResponse)
def analytics():
    processes = read_processes(1000)
    tx = read_transactions(1000)

    completed = [
        p for p in processes
        if p.get("actual_burst_ms") is not None
        and p.get("predicted_burst_ms") is not None
    ]

    errors = [
        float(p["predicted_burst_ms"]) - float(p["actual_burst_ms"])
        for p in completed
    ]
    mae = (
        sum(abs(e) for e in errors) / len(errors)
        if errors else None
    )
    rmse = (
        (sum(e * e for e in errors) / len(errors)) ** 0.5
        if errors else None
    )
    avg_wait = (
        sum(float(p.get("waiting_time_ms") or 0) for p in processes)
        / len(processes)
        if processes else None
    )

    ml_stats = {}
    if ml_model is not None:
        try:
            ml_stats = ml_model.metrics()
        except Exception as exc:
            ml_stats = {"error": str(exc)}

    content = f"""
    <h2>Analytics &amp; ML</h2>
    <div class="grid">
      <div class="metric"><div class="label">Processes with actual results</div>
        <div class="value">{len(completed)}</div></div>
      <div class="metric"><div class="label">MAE (ms)</div>
        <div class="value">{f"{mae:.2f}" if mae is not None else "N/A"}</div></div>
      <div class="metric"><div class="label">RMSE (ms)</div>
        <div class="value">{f"{rmse:.2f}" if rmse is not None else "N/A"}</div></div>
      <div class="metric"><div class="label">Average waiting time (ms)</div>
        <div class="value">{f"{avg_wait:.2f}" if avg_wait is not None else "N/A"}</div></div>
    </div>
    <div class="two">
      <section class="panel"><h3>Runtime by process</h3>
        <canvas id="runtimeChart"></canvas></section>
      <section class="panel"><h3>Rollback reasons</h3>
        <canvas id="rollbackChart"></canvas></section>
    </div>
    <section class="panel">
      <h3>ML module metrics</h3>
      <pre>{safe_text(json.dumps(ml_stats, indent=2))}</pre>
      <p class="muted">ML metrics depend on the training data available to
      your ML module. They are not guaranteed to be independent test-set
      metrics.</p>
    </section>
    <script>
    fetch('/api/analytics').then(r=>r.json()).then(d=>{{
      new Chart(document.getElementById('runtimeChart'),{{
        type:'bar',
        data:{{labels:d.runtime_labels||[],datasets:[
          {{label:'Predicted ms',data:d.predicted_values||[]}},
          {{label:'Actual ms',data:d.actual_values||[]}}
        ]}}
      }});
      new Chart(document.getElementById('rollbackChart'),{{
        type:'bar',
        data:{{labels:d.rollback_labels||[],datasets:[
          {{label:'Occurrences',data:d.rollback_values||[]}}
        ]}}
      }});
    }});
    </script>
    """
    return page(content, "Analytics & ML | SmartSched AI")


@APP.get("/logs", response_class=HTMLResponse)

@APP.get("/dbms-monitor", response_class=HTMLResponse)
def dbms_monitor():
    data = get_database_status()

    if data["connected"]:
        status = "CONNECTED"
        details = f"""
        <p><strong>Database:</strong> {safe_text(data["database"])}</p>
        <p><strong>Tables found:</strong> {len(data["tables"])}</p>
        <h3>Database table counts</h3>
        {table(["Table", "Stored rows"], [
            [name, count]
            for name, count in data["counts"].items()
        ])}
        <h3>Available tables</h3>
        {table(["Table name"], [[name] for name in data["tables"]])}
        """
    else:
        status = "CONNECTION ERROR"
        details = f"""
        <p><strong>Database:</strong> {safe_text(data["database"])}</p>
        <p><strong>Error:</strong> {safe_text(data["error"])}</p>
        <p>Check that MySQL is running and the username and password
        in your database settings are correct.</p>
        """

    content = f"""
    <h2>DBMS Monitor</h2>
    <section class="panel">
      <h3>MySQL connection status: {safe_text(status)}</h3>
      {details}
    </section>
    """
    return page(content, "DBMS Monitor | SmartSched AI")

def logs():
    tx = read_transactions(100)
    rows = [
        [
            t.get("created_at"), t.get("outcome"), t.get("pid"),
            t.get("error_code") or "—", t.get("message"),
        ]
        for t in tx
    ]
    content = f"""
    <h2>System Logs</h2>
    <section class="panel">
      <p class="muted">Transaction and rollback audit records.</p>
      {table(["Timestamp", "Event", "PID", "Code", "Message"], rows)}
    </section>
    """
    return page(content, "System Logs | SmartSched AI")


@APP.get("/api/health")
@APP.get("/health")
def health():
    info = {
        "ok": True,
        "app": "SmartSched AI",
        "database": "not checked",
        "cpp_scheduler": Path(SCHEDULER_EXE).exists(),
        "ml_module": ml_model is not None,
    }
    try:
        conn = db_connection()
        conn.close()
        info["database"] = "connected"
    except Exception as exc:
        info["ok"] = False
        info["database"] = "error"
        info["database_error"] = str(exc)

    return JSONResponse(info)


@APP.get("/api/processes")
def api_processes():
    try:
        return {"items": read_processes(200)}
    except Exception as exc:
        return JSONResponse(
            {"error": str(exc)}, status_code=503
        )


@APP.get("/api/transactions")
def api_transactions():
    try:
        return {"items": read_transactions(200)}
    except Exception as exc:
        return JSONResponse(
            {"error": str(exc)}, status_code=503
        )


@APP.get("/api/rules")
def api_rules():
    return {"items": ROLLBACK_RULES}


@APP.get("/api/advanced")
def api_advanced():
    return {
        "scheduler_executable": SCHEDULER_EXE,
        "scheduler_executable_found": Path(SCHEDULER_EXE).exists(),
        "ml_module_available": ml_model is not None,
        "note": (
            "Thermal values are simulated. This endpoint does not "
            "claim to read physical sensors or implement distributed 2PC."
        ),
    }


@APP.get("/api/metrics")
def api_metrics():
    if ml_model is None:
        return {
            "available": False,
            "message": "Save the supplied ML code as ml_model.py.",
        }

    try:
        return {"available": True, **ml_model.metrics()}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


@APP.get("/api/analytics")
def api_analytics():
    processes = read_processes(1000)
    tx = read_transactions(1000)

    committed = sum(1 for t in tx if t.get("outcome") == "COMMIT")
    rolled_back = sum(1 for t in tx if t.get("outcome") == "ROLLBACK")

    counts = {}
    for t in tx:
        if t.get("outcome") == "ROLLBACK":
            key = t.get("error_code") or "UNKNOWN"
            counts[key] = counts.get(key, 0) + 1

    recent = [
        p for p in processes
        if p.get("actual_burst_ms") is not None
    ][:20]
    recent.reverse()

    return {
        "process_count": len(processes),
        "committed": committed,
        "rolled_back": rolled_back,
        "rollback_labels": list(counts.keys()),
        "rollback_values": list(counts.values()),
        "runtime_labels": [str(p.get("pid")) for p in recent],
        "predicted_values": [
            float(p.get("predicted_burst_ms") or 0) for p in recent
        ],
        "actual_values": [
            float(p.get("actual_burst_ms") or 0) for p in recent
        ],
    }


@APP.get("/api/advanced/health")
def api_advanced_health():
    return health()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(APP, host="127.0.0.1", port=8000)
