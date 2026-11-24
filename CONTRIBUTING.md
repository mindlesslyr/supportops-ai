# Contributing to SupportOps AI

Thanks for helping improve the project. Keep changes small, testable, and tied to a real problem.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

The test suite uses temporary SQLite and embedded Qdrant instances. It does not require paid APIs.

## Before opening a pull request

Run the same checks used in CI:

```bash
python -m pytest -q
python -m compileall -q app tests
ruff check .
mypy app --ignore-missing-imports
bandit -q -r app
python -m json.tool n8n/supportops-workflow.json >/dev/null
docker compose config -q
```

## Change workflow

1. Search existing issues before creating a new one.
2. Create or claim an issue with clear acceptance criteria.
3. Create a focused branch such as `fix/webhook-replay` or `feat/document-delete`.
4. Add a failing regression test before fixing a bug or adding behavior.
5. Use conventional commits, for example `fix(api): reject malformed content length`.
6. Keep pull requests focused and explain how the change was verified.

Do not commit `.env` files, databases, Qdrant data, API keys, webhook secrets, customer content, or n8n credentials.

## Reporting security problems

Do not open a public issue for a vulnerability that could put deployed systems or user data at risk. Use GitHub's private vulnerability reporting feature when it is available for the repository.
