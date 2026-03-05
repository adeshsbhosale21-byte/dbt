# dbt MCP Analytics Agent

A premium, production-ready AI agent for querying and analyzing dbt projects using the Model Context Protocol (MCP).

## 📁 Project Structure

- **`/app`**: Core backend application (FastAPI + LangGraph).
- **`/frontend`**: React-based glassmorphic UI.
- **`/tests`**: Automation and verification scripts.
- **`/scripts`**: Utility scripts for debugging and schema inspection.
- **`/local_dbt_test`**: Sample dbt project for local development.

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+
- AWS Credentials (for Bedrock) or OpenAI API Key.

### 2. Installation
```bash
pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```

### 4. Running the App
Start the backend server from the project root using the module flag:
```bash
python -m app.main
```
Then visit: [http://localhost:8000](http://localhost:8000)

## 🛡️ Security
The agent includes built-in guardrails against destructive SQL commands and implements a Human-in-the-Loop (HITL) approval workflow for all data warehouse actions.
