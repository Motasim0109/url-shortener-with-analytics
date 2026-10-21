# URL Shortener with Click Analytics

A REST API for creating short links, managing link ownership, and tracking redirect counts.
Built with Python 3.13, FastAPI, PostgreSQL, SQLAlchemy, Redis, and Docker.

## Features

- Signup and login with Argon2 password hashing and JWT authentication.
- Link creation, owner-only link listings, and private click counts.
- Public redirects with unique custom aliases and optional expiration dates.
- Redis caching with PostgreSQL fallback when the cache is unavailable.
- Automated tests and GitHub Actions checks on pushes and pull requests.

## Design Decisions

- **PostgreSQL is the source of truth.** Alembic manages schema changes, and a database unique
  constraint prevents duplicate short codes.
- **Caching does not bypass correctness.** Cache lifetimes are capped by link expiration.
  Both cached and uncached redirects check expiration before recording a click.
- **Analytics remain persistent.** Every accepted redirect records a PostgreSQL click event,
  including cache hits. Expired links return `410 Gone` without recording clicks.
- **Access is scoped to the owner.** Redirects are public; link listings and analytics require
  authentication and enforce ownership.

## Run with Docker Compose

Start Docker Desktop with Linux containers enabled. Run commands from the project root;
no local Python installation is needed for this workflow.

Create or update `.env` beside `compose.yaml`, preserving any existing local settings:

```dotenv
JWT_SECRET_KEY=replace_with_a_random_secret
POSTGRES_PASSWORD=replace_with_a_random_hex_password
```

Use independently generated secrets, such as 32 random bytes encoded as hexadecimal for each value.
Hexadecimal avoids password URL-encoding issues. Keep `.env` private; it is excluded from Git and
the Docker build context.

Build the API image, start its dependencies, apply migrations, and start the API:

```powershell
docker compose build api
docker compose up -d db redis
docker compose run --rm api alembic upgrade head
docker compose up -d api
```

Open [Swagger UI](http://127.0.0.1:8000/docs). Sign up, log in, and enter the returned access token
in **Authorize** to create links and view analytics.

Only the API is exposed on `127.0.0.1:8000`; PostgreSQL and Redis use the internal Compose network.
The container database is separate from any existing local database.

- Rebuild after code changes: `docker compose up -d --build`.
- Apply new migrations before updating the API, using the startup sequence above.
- Stop the stack: `docker compose down`. Data persists in the `postgres_data` volume;
  adding `--volumes` deletes that storage.

## API Overview

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/v1/auth/signup` | Create an account |
| POST | `/api/v1/auth/login` | Obtain an access token |
| GET | `/api/v1/auth/me` | Retrieve the authenticated user |
| POST | `/api/v1/links` | Create a short link |
| GET | `/api/v1/links` | List the current user's links |
| GET | `/api/v1/links/{short_code}/analytics` | View an owned link's click count |
| GET | `/{short_code}` | Redirect to the destination |

Request schemas and error responses are documented in Swagger UI.

## Tests and CI

The 26-test suite covers authentication, ownership, alias validation, expiration, redirects,
click recording, and cache behavior. Database tests use a separate PostgreSQL instance and
roll back changes after each test. Redirect tests mock Redis; no running API or Redis is required.

With a Python 3.13 virtual environment active and Docker running, install the development
dependencies and start the test database:

```powershell
python -m pip install -e ".[dev]"
docker compose -f compose.test.yaml -p url-shortener-tests up -d db_test
```

Once PostgreSQL is ready, run the tests, linting, and formatting checks:

```powershell
python -m pytest tests -v
ruff check .
ruff format --check .
```

Tests apply Alembic migrations automatically. [GitHub Actions](.github/workflows/ci.yml) runs
the same checks with a PostgreSQL service on every push and pull request.
