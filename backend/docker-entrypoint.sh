#!/bin/bash
set -e

echo "Starting lanjian backend..."

# Wait for PostgreSQL
echo "Waiting for database connection..."
max_retries=30
retry_count=0

while [ $retry_count -lt $max_retries ]; do
    if .venv/bin/python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
import os

async def check_db():
    engine = create_async_engine(os.environ.get('DATABASE_URL', ''))
    try:
        async with engine.connect() as conn:
            await conn.execute(text('SELECT 1'))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()

from sqlalchemy import text
exit(0 if asyncio.run(check_db()) else 1)
" 2>/dev/null; then
        echo "Database connection successful!"
        break
    fi

    retry_count=$((retry_count + 1))
    echo "   Retry $retry_count/$max_retries..."
    sleep 2
done

if [ $retry_count -eq $max_retries ]; then
    echo "Cannot connect to database. Please check DATABASE_URL configuration."
    exit 1
fi

# Run database migrations
echo "Running database migrations..."
.venv/bin/alembic upgrade head

echo "Database migrations completed!"

# Start uvicorn
echo "Starting API server..."
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
