# Contributing to OmniMCP Router

Thank you for considering contributing! Here's how to get started.

## Setup

```bash
git clone https://github.com/Simonc44/OmniMCP.git
cd OmniMCP
pip install -r requirements.txt
pip install black isort
```

## Running Tests

Always run the integration tests before submitting a PR:

```bash
python run_integration_test.py
```

The test suite validates aggregation, async parallelism, performance monitoring, auto-healing, and hot-reload.

## Code Style

This project uses [Black](https://github.com/psf/black) for formatting:

```bash
black router.py mock_server.py run_integration_test.py
isort router.py
```

## Pull Request Guidelines

1. **Fork** the repository
2. Create a **feature branch**: `git checkout -b feat/my-feature`
3. Write **clear commit messages**: `feat: add WebSocket transport support`
4. Ensure **all tests pass**: `python run_integration_test.py`
5. Open a PR against `main` with a description of what you changed and why

## Commit Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation only
- `refactor:` — code refactor without behavior change
- `test:` — tests only
- `chore:` — maintenance tasks

## What to Contribute

Check open issues for ideas. High-value contributions:

- **WebSocket / HTTP transport**: alternative to stdio for remote deployment
- **Metrics endpoint**: expose Prometheus-compatible tool call stats
- **Config schema validation**: validate `mcp_router_config.json` on load
- **Plugin hooks**: allow external Python modules to register hooks
- **Docker support**: `Dockerfile` + `docker-compose.yml`

## Code of Conduct

Be respectful. We're all here to build something useful.