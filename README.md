# SmartSched AI

### ML-Orchestrated CPU Scheduling with Concurrent OS and ACID DBMS Integration

SmartSched AI is an operating-system scheduling simulator that combines CPU scheduling algorithms, machine-learning-based CPU burst prediction, database-backed execution logging, and process resource management.

## Features

- **CPU Scheduling:** Simulates scheduling decisions using the implemented scheduling policies.
- **Machine Learning:** Uses Random Forest regression to predict CPU burst time when a trained model is available, with a heuristic fallback.
- **Process Management:** Handles process attributes, scheduling queues, and simulated CPU-core assignment.
- **Database Integration:** Uses MySQL for database connectivity and persistent process and execution records where implemented.
- **Transaction and Rollback Simulation:** Demonstrates validation failures, execution timeouts, rollback conditions, and audit logging.
- **Performance Metrics:** Tracks scheduling metrics and evaluates prediction errors using MAE and RMSE.
- **GUI/API Integration:** Provides the interface and backend capabilities supported by the current implementation.

## Technologies

- C++
- Python
- MySQL
- scikit-learn
- joblib
- Git

## Project Files

- `os.cpp` — C++ scheduling and process simulation
- `gui_app.py` — GUI/application entry point
- `ml_model.py` — Machine-learning prediction and evaluation
- `dbms_connection.py` — MySQL connection and database status checks
- `schema.sql` — SQL schema
- `requirements.txt` — Python dependencies

## Setup

1. Install Python 3, a C++ compiler, and MySQL Server.
2. Clone this repository.
3. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Configure MySQL and initialize the required database schema.
5. Configure database credentials using the supported environment variables.
6. Compile the C++ scheduler if required by the application.
7. Run the application using its configured entry point.

## Machine Learning

The ML module uses Random Forest regression to estimate CPU burst time from process features. If a trained model is unavailable, the implementation falls back to a heuristic prediction. Model evaluation includes Mean Absolute Error (MAE) and Root Mean Squared Error (RMSE).

## Project Status

The project includes implemented scheduling, database, and prediction components. Further enhancements include adaptive CPU scheduling, continuous model evaluation and retraining, resource overload prediction, and ML-assisted core balancing, according to their current implementation status.

## Team

Team Nexus — T046

Add or update team member details as appropriate.
