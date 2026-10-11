
#include <algorithm>
#include <cmath>
#include <functional>
#include <iomanip>
#include <iostream>
#include <map>
#include <queue>
#include <regex>
#include <sstream>
#include <string>
#include <vector>

using namespace std;

string input;

double numberValue(const string& key, double fallback) {
    regex pattern("\"" + key +
        "\"\\s*:\\s*(-?[0-9]+(?:\\.[0-9]+)?)");
    smatch match;
    if (regex_search(input, match, pattern))
        return stod(match[1]);
    return fallback;
}

string stringValue(const string& key, const string& fallback) {
    regex pattern("\"" + key + "\"\\s*:\\s*\"([^\"]*)\"");
    smatch match;
    if (regex_search(input, match, pattern))
        return match[1];
    return fallback;
}

bool boolValue(const string& key) {
    regex pattern("\"" + key + "\"\\s*:\\s*true");
    return regex_search(input, pattern);
}

string escapeJson(const string& value) {
    string result;
    for (char c : value) {
        if (c == '\\' || c == '"') result += '\\';
        if (c == '\n') {
            result += "\\n";
            continue;
        }
        if (c == '\r') {
            result += "\\r";
            continue;
        }
        result += c;
    }
    return result;
}

struct Process {
    int pid = 1;
    int arrival = 0;
    int burst = 1;
    int priority = 2;
    int io = 30;
    int core = 1;
    int start = 0;
    int finish = 0;
    int waiting = 0;
    int turnaround = 0;
    int remaining = 0;
};

bool hasCycle(const map<string, vector<string>>& graph) {
    map<string, int> state;

    function<bool(const string&)> visit = [&](const string& node) {
        if (state[node] == 1) return true;
        if (state[node] == 2) return false;

        state[node] = 1;
        auto found = graph.find(node);

        if (found != graph.end()) {
            for (const string& next : found->second)
                if (visit(next)) return true;
        }

        state[node] = 2;
        return false;
    };

    for (const auto& entry : graph)
        if (visit(entry.first)) return true;

    return false;
}

string queueFor(int priority, int io) {
    if (priority == 1 || io >= 60) return "Q0 - Interactive";
    if (priority <= 3) return "Q1 - Standard";
    return "Q2 - Batch";
}

int quantumFor(const string& queueName) {
    if (queueName.find("Q0") == 0) return 10;
    if (queueName.find("Q1") == 0) return 20;
    return 40;
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    ostringstream stream;
    stream << cin.rdbuf();
    input = stream.str();

    if (input.empty()) {
        cerr << "No JSON request received on stdin.\n";
        return 2;
    }

    int pid = static_cast<int>(numberValue("pid", 1));
    int arrival = static_cast<int>(numberValue("arrival_ms", 0));
    int ram = static_cast<int>(numberValue("ram_mb", 512));
    int disk = static_cast<int>(numberValue("disk_mb", 100));
    int quota = static_cast<int>(numberValue("disk_quota_mb", 1024));
    int priority = static_cast<int>(numberValue("priority", 2));
    int burst = static_cast<int>(numberValue("max_burst_ms", 100));
    int actual = static_cast<int>(numberValue("actual_ms", 35));
    int io = static_cast<int>(numberValue("io_ratio", 30));
    int maxWait = static_cast<int>(numberValue("max_wait_ms", 500));
    int cores = max(1, min(4, static_cast<int>(
        numberValue("cores", 4))));

    string algorithm = stringValue("algorithm", "MLFQ");
    string rollbackName, errorCode, message;

    // Six rollback rules. The first matching rule wins.
    if (ram > 2048) {
        rollbackName = "Memory Capacity Exceeded";
        errorCode = "CONSISTENCY_VIOLATION";
        message = "CONSISTENCY_VIOLATION: Requested RAM exceeds 2048 MB limit";
    } else if (priority < 1 || priority > 5) {
        rollbackName = "Priority Boundary Exceeded";
        errorCode = "CONSTRAINT_VIOLATION";
        message = "CONSTRAINT_VIOLATION: Priority level out of valid bounds (1-5)";
    } else if (actual > burst) {
        rollbackName = "Execution Timeout Exceeded";
        errorCode = "TIMEOUT_EXCEEDED";
        message = "TIMEOUT_EXCEEDED: Actual execution time breached max burst limit";
    } else if (boolValue("simulate_deadlock")) {
        rollbackName = "Deadlock / Wait Time Exceeded";
        errorCode = "DEADLOCK_ABORT";
        message = "DEADLOCK_ABORT: Process exceeded maximum allowable wait time";
    } else if (disk > quota) {
        rollbackName = "Disk Storage Exceeded";
        errorCode = "QUOTA_VIOLATION";
        message = "QUOTA_VIOLATION: Process storage block allocation exceeded disk quota";
    } else if (boolValue("inject_fault")) {
        rollbackName = "Simulated Runtime Exception";
        errorCode = "RUNTIME_FAULT";
        message = "RUNTIME_FAULT: Simulated OS kernel thread execution failure";
    }

    double predicted = round(burst * 0.85 * 100.0) / 100.0;
    double waiting = arrival + max(0, priority - 1) * 5;
    double turnaround = waiting + actual;
    double error = abs(predicted - actual);
    double temperature = round((45.0 + min(60.0, actual / 2.0)) * 10) / 10;
    double power = round((12.0 + actual * 0.2) * 10) / 10;

    // Optional thermal rollback is a simulation, not a hardware sensor.
    if (errorCode.empty() && boolValue("simulate_thermal") &&
        temperature > 85.0) {
        rollbackName = "Thermal Throttling";
        errorCode = "THERMAL_THROTTLE";
        message = "THERMAL_THROTTLE: Simulated core temperature exceeded 85 C";
    }

    string queueName = queueFor(priority, io);
    int quantum = quantumFor(queueName);

    if (!errorCode.empty()) {
        cout << fixed << setprecision(2)
             << "{\"ok\":false"
             << ",\"pid\":" << pid
             << ",\"status\":\"ROLLED_BACK\""
             << ",\"acid_status\":\"ROLLED_BACK\""
             << ",\"rollback_condition\":\"" << escapeJson(rollbackName) << "\""
             << ",\"error_code\":\"" << escapeJson(errorCode) << "\""
             << ",\"dbms_message\":\"" << escapeJson(message) << "\""
             << ",\"algorithm\":\"" << escapeJson(algorithm) << "\""
             << ",\"predicted_ms\":" << predicted
             << ",\"actual_ms\":" << actual
             << ",\"error_ms\":" << error
             << ",\"waiting_ms\":" << waiting
             << ",\"turnaround_ms\":" << turnaround
             << ",\"estimated_temperature_c\":" << temperature
             << ",\"simulated_power_watts\":" << power
             << "}\n";
        return 0;
    }

    // Approximate workload balancing across the configured cores.
    // A standalone invocation has no persistent history, so all core
    // loads start at zero unless load values are supplied in the input.
    vector<double> loads(cores, 0.0);
    for (int i = 0; i < cores; ++i) {
        string key = "core" + to_string(i + 1) + "_load_ms";
        loads[i] = max(0.0, numberValue(key, 0.0));
    }

    int core = static_cast<int>(
        min_element(loads.begin(), loads.end()) - loads.begin()) + 1;
    loads[core - 1] += actual;

    Process process;
    process.pid = pid;
    process.arrival = arrival;
    process.burst = actual;
    process.priority = priority;
    process.io = io;
    process.core = core;
    process.start = arrival + static_cast<int>(waiting);
    process.finish = process.start + actual;
    process.waiting = static_cast<int>(waiting);
    process.turnaround = static_cast<int>(turnaround);

    // Algorithm label and scheduling policy metadata.
    // This executable handles one submitted process per invocation;
    // true multi-process scheduling requires a process batch/queue input.
    string schedulingNote;

    if (algorithm == "FCFS") {
        schedulingNote = "First Come First Served";
    } else if (algorithm == "SJF") {
        schedulingNote = "Shortest Job First; single-request demonstration";
    } else if (algorithm == "Priority") {
        schedulingNote = "Priority Scheduling; 1 is highest priority";
    } else if (algorithm == "Round Robin") {
        schedulingNote = "Round Robin; quantum reported for demonstration";
    } else {
        algorithm = "MLFQ";
        schedulingNote = "Three-level feedback queue classification";
    }

    cout << fixed << setprecision(2)
         << "{\"ok\":true"
         << ",\"pid\":" << pid
         << ",\"core\":" << core
         << ",\"status\":\"COMMITTED\""
         << ",\"acid_status\":\"COMMITTED\""
         << ",\"dbms_message\":\"Committed Successfully\""
         << ",\"ram_mb\":" << ram
         << ",\"disk_mb\":" << disk
         << ",\"priority\":" << priority
         << ",\"predicted_ms\":" << predicted
         << ",\"actual_ms\":" << actual
         << ",\"error_ms\":" << error
         << ",\"waiting_ms\":" << waiting
         << ",\"turnaround_ms\":" << turnaround
         << ",\"algorithm\":\"" << escapeJson(algorithm) << "\""
         << ",\"scheduling_note\":\"" << escapeJson(schedulingNote) << "\""
         << ",\"queue\":\"" << escapeJson(queueName) << "\""
         << ",\"quantum_ms\":" << quantum
         << ",\"timeline\":{\"start_ms\":" << process.start
         << ",\"end_ms\":" << process.finish << "}"
         << ",\"estimated_temperature_c\":" << temperature
         << ",\"simulated_power_watts\":" << power
         << ",\"core_loads_ms\":[";

    for (int i = 0; i < cores; ++i) {
        if (i) cout << ",";
        cout << loads[i];
    }

    cout << "]}\n";
    return 0;
}
