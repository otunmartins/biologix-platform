# Web platform setup

The web platform provides account signup, login, logout, experiment creation, experiment history, and experiment detail pages. Account and experiment data is stored in PostgreSQL. Every experiment query includes the authenticated account identifier so records are isolated between users.

## Start with Docker

Docker Desktop or Docker Engine with Compose is required. From the repository root, run:

```bash
cp .env.example .env
docker compose up --build postgres redis api worker frontend
```

Open `http://localhost:3000`. The API documentation is available at `http://localhost:8000/docs`.

Set `SECRET_KEY` in `.env` to a long random value before using the application outside local development. Set `COOKIE_SECURE=true` when the API is served through HTTPS.

PostgreSQL data is kept in the `postgres-data` Docker volume. Database migrations run automatically when the API container starts.

## Run services directly

Start PostgreSQL and set `DATABASE_URL`. Then install the API dependencies and apply migrations:

```bash
pip install -e ".[api,dev]"
alembic upgrade head
uvicorn biologix_ai.http_api.app:app --reload
```

In another terminal, start the web application:

```bash
cd frontend
npm install
npm run dev
```

The frontend sends API requests through the Next.js server. Set `API_URL` when building the frontend if the API is not available at `http://localhost:8000`.

## Tests

Platform API tests use an isolated in memory database:

```bash
pytest tests/platform
```

The frontend production build performs TypeScript validation:

```bash
cd frontend
npm run build
```
