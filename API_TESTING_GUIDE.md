# PulseEngine — API Testing Guide

## Table of Contents

1. [Setup](#setup)
2. [Docker Run Instructions](#docker-run-instructions)
3. [Environment Variables](#environment-variables)
4. [Endpoint Reference](#endpoint-reference)
5. [Manual Validation Walkthrough](#manual-validation-walkthrough)
6. [Running Tests](#running-tests)

---

## Setup

### Prerequisites

- Docker & Docker Compose (v2+)
- `curl` or Postman for API testing
- `redis-cli` (optional, for inspecting Redis state)

### Initial Setup

```bash
# Clone the project
cd PulseEngine

# Copy the example env file
cp .env.example .env

# Edit .env as needed (defaults work for Docker)
```

---

## Docker Run Instructions

### Start all services

```bash
docker compose up --build -d
```

This starts 6 services:

| Service         | Description                 | Port |
|-----------------|-----------------------------|------|
| `web`           | Django + Gunicorn           | 8000 |
| `postgres`      | PostgreSQL 16               | 5432 |
| `redis`         | Redis 7                     | 6379 |
| `elasticsearch` | Elasticsearch 8.13          | 9200 |
| `celery_worker` | Celery worker (4 processes) | —    |
| `celery_beat`   | Celery Beat scheduler       | —    |

### Create a superuser

```bash
docker compose exec web uv run python manage.py createsuperuser
```

### Stop services

```bash
docker compose down
```

### View logs

```bash
docker compose logs -f web
docker compose logs -f celery_worker
docker compose logs -f celery_beat
```

---

## Environment Variables

All variables are loaded from `.env`. If the file is missing, the application **fails loudly**.

| Variable              | Required | Description                     | Example                          |
|-----------------------|----------|---------------------------------|----------------------------------|
| `SECRET_KEY`          | ✅       | Django secret key               | `change-me-to-a-real-secret-key` |
| `DEBUG`               | ✅       | Debug mode (True/False)         | `True`                           |
| `ALLOWED_HOSTS`       | ✅       | Comma-separated allowed hosts   | `localhost,127.0.0.1`            |
| `DATABASE_NAME`       | ✅       | PostgreSQL database name        | `pulseengine`                    |
| `DATABASE_USER`       | ✅       | PostgreSQL user                 | `pulseengine`                    |
| `DATABASE_PASSWORD`   | ✅       | PostgreSQL password             | `pulseengine`                    |
| `DATABASE_HOST`       | ❌       | PostgreSQL host (default: localhost) | `postgres`                  |
| `DATABASE_PORT`       | ❌       | PostgreSQL port (default: 5432) | `5432`                           |
| `REDIS_URL`           | ✅       | Redis connection URL            | `redis://redis:6379/0`           |
| `ELASTICSEARCH_URL`   | ✅       | Elasticsearch URL               | `http://elasticsearch:9200`      |
| `CELERY_BROKER_URL`   | ✅       | Celery broker URL               | `redis://redis:6379/1`           |
| `CELERY_RESULT_BACKEND`| ✅      | Celery result backend URL       | `redis://redis:6379/2`           |

---

## Endpoint Reference

### Categories

#### Create Category

```
POST /categories/
Content-Type: application/json

{
    "name": "Technology"
}
```

**Response (201):**

```json
{
    "id": 1,
    "name": "Technology",
    "slug": "technology"
}
```

#### List Categories

```
GET /categories/
```

**Response (200):**

```json
[
    {"id": 1, "name": "Technology", "slug": "technology"},
    {"id": 2, "name": "Science", "slug": "science"}
]
```

#### Retrieve Category

```
GET /categories/{slug}/
```

---

### Posts

#### Create Post

```
POST /posts/
Content-Type: application/json

{
    "category": 1,
    "content": "This is my post about emerging tech trends."
}
```

**Response (201):**

```json
{
    "id": 1,
    "category": 1,
    "content": "This is my post about emerging tech trends.",
    "created_at": "2026-03-01T12:00:00Z",
    "is_flagged": false
}
```

> **Note:** Requires authentication. The `author` is set from the authenticated user.

#### List Posts

```
GET /posts/list/
```

#### Retrieve Post

```
GET /posts/{id}/
```

---

### Engagement

All engagement endpoints require authentication.

#### Like a Post

```
POST /posts/{id}/like/
```

**Response (201):**

```json
{
    "detail": "Liked.",
    "post_id": 1,
    "type": "LIKE"
}
```

**Response (409) — Duplicate Like:**

```json
{
    "detail": "You have already liked this post."
}
```

#### Comment on a Post

```
POST /posts/{id}/comment/
```

**Response (201):**

```json
{
    "detail": "Commented.",
    "post_id": 1,
    "type": "COMMENT"
}
```

#### Share a Post

```
POST /posts/{id}/share/
```

**Response (201):**

```json
{
    "detail": "Shared.",
    "post_id": 1,
    "type": "SHARE"
}
```

---

### Feed

#### Global Feed

```
GET /feed/
```

**Response (200):** Array of top 20 posts ranked by trending score.

```json
[
    {
        "id": 1,
        "author": 1,
        "author_username": "alice",
        "category": 1,
        "category_slug": "technology",
        "content": "...",
        "created_at": "2026-03-01T12:00:00Z",
        "is_flagged": false,
        "trending_score": 45.2
    }
]
```

#### Category Feed

```
GET /feed/{slug}/
```

**Response (200):** Same format, filtered by category.

---

### Search

#### Search Posts

```
GET /search/?q=python&category=technology
```

| Param      | Required | Description              |
|------------|----------|--------------------------|
| `q`        | ✅       | Search query string       |
| `category` | ❌       | Filter by category slug   |

**Response (200):**

```json
[
    {
        "id": 1,
        "author": 1,
        "author_username": "alice",
        "category": 1,
        "category_slug": "technology",
        "content": "Python programming guide...",
        "created_at": "2026-03-01T12:00:00Z",
        "is_flagged": false,
        "search_score": 0.8523
    }
]
```

---

## Manual Validation Walkthrough

### Step 1: Create Categories

```bash
curl -X POST http://localhost:8000/categories/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Technology"}'

curl -X POST http://localhost:8000/categories/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Science"}'

curl -X POST http://localhost:8000/categories/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Sports"}'
```

### Step 2: Create a Superuser and Get Auth

```bash
docker compose exec web uv run python manage.py createsuperuser
# Use: admin / admin@example.com / adminpass123

# For API calls, use session auth or DRF's browsable API at:
# http://localhost:8000/admin/
```

### Step 3: Create 3 Posts

```bash
# Login to admin first, then use session cookie, or use Django shell:
docker compose exec web uv run python manage.py shell -c "
from django.contrib.auth import get_user_model
from posts.models import Post
from categories.models import Category

User = get_user_model()
user = User.objects.first()
tech = Category.objects.get(slug='technology')
sci = Category.objects.get(slug='science')
sports = Category.objects.get(slug='sports')

Post.objects.create(author=user, category=tech, content='AI is transforming the world')
Post.objects.create(author=user, category=sci, content='New exoplanet discovered')
Post.objects.create(author=user, category=sports, content='World Cup finals recap')
print('Created 3 posts')
"
```

### Step 4: Like One Post Heavily

```bash
docker compose exec web uv run python manage.py shell -c "
from django.contrib.auth import get_user_model
from engagement.models import EngagementEvent, EngagementType, UserPostLike
from posts.models import Post
import redis, time
from django.conf import settings

User = get_user_model()
post = Post.objects.first()  # The tech post
r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

for i in range(20):
    u, _ = User.objects.get_or_create(username=f'liker{i}', defaults={'password': 'pass'})
    try:
        UserPostLike.objects.create(post=post, user=u)
        EngagementEvent.objects.create(post=post, user=u, type=EngagementType.LIKE)
        r.hincrby(f'post:{post.pk}:engagement', 'likes', 1)
        r.zadd('ranking:dirty_posts', {str(post.pk): time.time()})
    except Exception:
        pass

print(f'Added 20 likes to post {post.pk}')
"
```

### Step 5: Wait 5+ Seconds

```bash
echo "Waiting for Celery Beat to process dirty posts..."
sleep 10
```

### Step 6: Call /feed/ and Confirm Ranking

```bash
curl http://localhost:8000/feed/ | python -m json.tool
```

The heavily-liked post should appear first.

### Step 7: Inspect Redis

```bash
# Connect to Redis
docker compose exec redis redis-cli

# View global leaderboard
ZREVRANGE ranking:global 0 10 WITHSCORES

# View category leaderboard
ZREVRANGE ranking:category:technology 0 10 WITHSCORES

# View engagement counters for post 1
HGETALL post:1:engagement

# View dirty posts set
ZRANGE ranking:dirty_posts 0 -1 WITHSCORES

# Exit
exit
```

### Step 8: Test Rebuild Command

```bash
# Clear Redis
docker compose exec redis redis-cli FLUSHDB

# Verify feed is empty or falls back
curl http://localhost:8000/feed/ | python -m json.tool

# Run rebuild
docker compose exec web uv run python manage.py rebuild_leaderboards

# Verify state is restored
docker compose exec redis redis-cli ZREVRANGE ranking:global 0 10 WITHSCORES

# Feed should work again
curl http://localhost:8000/feed/ | python -m json.tool
```

### Step 9: Test Fraud Suppression

```bash
docker compose exec web uv run python manage.py shell -c "
from django.contrib.auth import get_user_model
from engagement.models import EngagementEvent, EngagementType
from posts.models import Post
import redis, time
from django.conf import settings

User = get_user_model()
post = Post.objects.last()  # Pick a post
r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

# Simulate extreme engagement (200+ events in <1 min)
for i in range(250):
    u, _ = User.objects.get_or_create(username=f'fraud{i}', defaults={'password': 'pass'})
    EngagementEvent.objects.create(post=post, user=u, type=EngagementType.LIKE)
    r.hincrby(f'post:{post.pk}:engagement', 'likes', 1)

r.zadd('ranking:dirty_posts', {str(post.pk): time.time() - 10})
print(f'Simulated fraud on post {post.pk}')
"

# Wait for worker to process
sleep 10

# Check if post is flagged
docker compose exec web uv run python manage.py shell -c "
from posts.models import Post
p = Post.objects.last()
print(f'Post {p.pk} is_flagged: {p.is_flagged}')
"

# Flagged post should NOT appear in global feed
curl http://localhost:8000/feed/ | python -m json.tool
```

---

## Running Tests

### Inside Docker

```bash
docker compose exec web uv run pytest --cov=. --cov-report=term-missing -v
```

### Locally (with services running)

```bash
uv sync
uv run pytest --cov=. --cov-report=term-missing -v
```

### Coverage Target

Minimum **85% coverage** required. Check with:

```bash
uv run pytest --cov=. --cov-report=term-missing
```








