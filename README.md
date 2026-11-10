SupportOps AI

A production-style RAG customer-support automation platform built with FastAPI, n8n, Qdrant, PostgreSQL and LLM integrations.

Current Features
FastAPI application
Health-check endpoint
Automated API test
Project Structure
supportops-ai/
├── app/
│   └── main.py
├── tests/
│   └── test_health.py
├── requirements.txt
└── requirements-dev.txt

## Run Locally

Activate the virtual environment:

```bash
source .venv/bin/activate
Start the API:

uvicorn app.main:app --reload
Open the API documentation:

http://127.0.0.1:8000/docs

Run Tests
python -m pytest -q
Save it, then run:

git status

You should now see the README and requirements files alongside `app/` and `tests/`.