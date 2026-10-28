# Contributing to RefExposer

Thanks for taking the time. This file is short on purpose: it says what you need to run the project, what a
change is expected to look like, and how it gets reviewed.

## Before you write code

- **A bug**: open an issue with what you did, what happened and what you expected. The version
  (`GET /api/system`, or the footer of the interface) and the relevant log lines help more than anything else.
- **A feature**: open an issue first and say what problem it solves. RefExposer has a lot of surface already,
  and the cheapest change is the one that turns out to be unnecessary.
- **A security issue**: do not open an issue. See [SECURITY.md](SECURITY.md).

Small, obvious fixes (a typo, a wrong link, an off-by-one in a message) need no issue — send the pull request.

## Running it

Docker is the only prerequisite; nothing is installed on the host.

```bash
cp .env.example .env            # REFEX_SECRET_KEY and the admin password at least
docker compose up -d            # or: make up      → http://localhost:8080
make test                       # backend test suite
make typecheck                  # type-check and build the frontend
```

Running the backend or the frontend directly on your machine is described in
**Development** in the [documentation](docs/development.md#development).

## What a change should look like

- **Tests.** A behaviour that matters has a test. The suite is fast and runs offline; the fixtures in
  `backend/tests/conftest.py` give you a running application with referentials in a few lines. A bug fix comes
  with the test that was failing.
- **Keep the diff about one thing.** Reformatting, renaming and fixing in one pull request makes the change
  impossible to review.
- **Match the surrounding code.** Same naming, same comment density, same idiom. Comments explain *why*, never
  what the line below already says, and never what changed — that is what the history is for.
- **English** in the code, the comments, the commit messages and the interface.
- **Type hints** on the Python side (the code is written for Python 3.12), **TypeScript** with no `any` on the
  frontend; `make typecheck` must pass.
- **Authorization goes through the existing choke points.** `CurrentUser.level()` decides who may read a
  referential, and nothing bypasses it — not a new endpoint, not a tool, not a background job. If you need a
  new kind of restriction, add it there rather than in a caller.
- **A migration** for every change to `backend/app/models.py`:
  `docker compose exec backend alembic revision --autogenerate -m "what it does"`, then read what it generated.
  It must work on SQLite and PostgreSQL alike (`batch_alter_table`).
- **Dependencies.** Edit `backend/requirements.in` (or `requirements-dev.in`) and run `make lock`: the
  `.txt` files are generated and carry hashes. On the frontend, keep `package-lock.json` in the commit.

## Documentation

A feature that is not documented does not exist for the person who has to operate it. A change that adds or
alters behaviour updates, as needed:

- the [README](README.md) — the reference, written for whoever runs the instance;
- the in-application guides (`frontend/src/pages/help/`) — written for whoever uses it;
- the docstring at the top of the module, when the change is about *how* something works.

## Pull requests

- Branch off `main`, one subject per branch.
- Say in the description what the change does and why, and how you checked it.
- CI runs the backend tests and the frontend build on every push. Keep it green.
- By opening a pull request you agree that your contribution is licensed under the
  [Apache License 2.0](LICENSE), like the rest of the project. There is no CLA.

Source files carry no licence header: `LICENSE` covers the whole repository.

## Code of conduct

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
