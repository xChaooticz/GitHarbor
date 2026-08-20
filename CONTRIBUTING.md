# Contributing to GitHarbor

Thank you for helping improve GitHarbor. Please open an issue before a large behavioral change.

## Development

1. Install Python 3.12+, Git, and Docker.
2. Create a virtual environment and run `pip install -e ".[dev]"`.
3. Copy `.env.example` to `.env` when running the application. Tests need no credentials.
4. Run `pytest`, `ruff check .`, `ruff format --check .`, and `mypy githarbor`.

Keep preservation guarantees intact: discovery absence must never delete a Gitea repository. Tests
must mock external services, avoid secrets, and cover new reconciliation behavior. Never include
real API tokens or private repository data in issues, fixtures, logs, or commits.

By contributing, you agree that your contribution is licensed under the MIT License.
