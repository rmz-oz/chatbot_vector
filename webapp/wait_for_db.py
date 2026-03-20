"""Wait until PostgreSQL is ready before starting Django."""
import os
import time
import psycopg2

config = {
    "dbname":   os.environ.get("POSTGRES_DB",       "chatbot"),
    "user":     os.environ.get("POSTGRES_USER",     "chatbot"),
    "password": os.environ.get("POSTGRES_PASSWORD", "chatbot"),
    "host":     os.environ.get("POSTGRES_HOST",     "db"),
    "port":     os.environ.get("POSTGRES_PORT",     "5432"),
}

print("Waiting for database…", flush=True)
for attempt in range(30):
    try:
        conn = psycopg2.connect(**config)
        conn.close()
        print("Database is ready.", flush=True)
        break
    except psycopg2.OperationalError:
        print(f"  attempt {attempt + 1}/30 — not ready yet", flush=True)
        time.sleep(2)
else:
    print("Could not connect to database after 60s. Exiting.", flush=True)
    raise SystemExit(1)
