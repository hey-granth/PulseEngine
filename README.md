# PulseEngine

Distributed Trending & Ranking Backend

## Overview

PulseEngine solves a specific architectural problem: implementing real-time trending rankings across categories when engagement traffic is bursty and concurrent.

### The Problem

A naive "Hot Topics" system sorts posts by engagement count at query time. This fails at scale because:

1. **Write Amplification**: Every like/comment/share triggers a re-calculation affecting rankings across multiple categories. A single viral post can cause 100+ engagement events per second, each invalidating cached results.

2. **Ranking Storms**: Re-ranking all posts on every engagement creates CPU spikes that ripple through feed queries.

3. **Cache Coherence**: Keeping trending scores synchronized across PostgreSQL (transactional source), Redis (ranking state), and Elasticsearch (search) without races is non-trivial.

4. **Time Decay Complexity**: Trending must decay older posts. Recomputing decay on every read is expensive; recomputing on every write is wasteful.

5. **Fraud Surface**: Engagement velocity spikes are exploitable. A naive system rewards bots equally to legitimate users.

### Constraints

- **User Scale**: Expected 1M+ monthly active users
- **Engagement Rate**: 10-50 events per post per day (volatile)
- **Category Count**: Small (5-20 categories, not thousands)
- **Latency**: Feed queries must return in <100ms
- **Consistency**: Rankings must reflect database truth even after Redis failure

### Design Philosophy

PulseEngine uses:

- **PostgreSQL as source of truth**: All engagement events are durably logged. Rankings can be rebuilt from DB alone.
- **Redis as ranking cache**: Sorted sets for efficient leaderboard queries and merges.
- **Debounced workers**: Instead of recalculating on every event, accumulate changes and process in batches every 5 seconds.
- **Elasticsearch for search**: Separate concern from ranking; re-ranked at query time using Redis scores.

This trades per-event latency (ranking takes 5s to fully propagate) for predictable, non-bursty workloads.

## Core Design Challenges

### 1. Write Path Amplification

**Problem**: Without debouncing, 100 rapid likes on one post means:
- 100 database writes (EngagementEvent inserts)
- 100 Redis counter increments
- 100 ranking recalculations
- 100 category leaderboard updates
- 100 global merge evaluations

Total: 500+ operations for what logically is "this post got 100 likes."

**Alternative Rejected**: Synchronous scoring on every like. This worked for <1K posts but became unscalable at category-level aggregation.

**Final Approach**: 
- Write EngagementEvent to DB (durable, indexed)
- Atomic increment Redis counter (fast)
- ZADD post to `ranking:dirty_posts` with current timestamp
- Worker runs every 5 seconds, pulls posts older than 5s, computes score once per post, updates category leaderboard

This reduces 100 events to 1 ranking calculation.

### 2. Ranking Recalculation Race Conditions

**Problem**: Between when a post is marked dirty and when the worker processes it, new events may arrive. Processing stale counters loses recent data.

**Alternative Rejected**: Lock-based synchronization (deadlock risk) or versioning (increased memory).

**Final Approach**: 
- Worker reads counter state at processing time
- If post marked dirty again after processing, next cycle picks it up
- Because cycle is 5s, maximum staleness is 5s
- This is acceptable for trending (users expect ~5s propagation)

### 3. Global Leaderboard Merge Consistency

**Problem**: Global leaderboard is computed from category leaderboards. Between computing category scores and publishing global:
- A post could be flagged (should be excluded)
- A category leaderboard could change
- An older transaction could partially apply

**Alternative Rejected**: Multi-key Redis transactions (not available; WATCH is insufficient at scale).

**Final Approach**: 
- Read all category leaderboards in memory
- Merge in Python (deterministic)
- Delete old global key
- Write new global key atomically via pipeline
- If failure mid-merge, worst case is old global leaderboard persists until next cycle (10s)

### 4. Redis Memory Scaling

**Problem**: Naive design stores every engagement event in Redis. With 1M users and 50 events/user/day, that's 50M events daily. Each event object consumes 100+ bytes → 5GB/day.

**Alternative Rejected**: Store events in Redis cache (lost on restart).

**Final Approach**: 
- Store only counters (hash per post: likes, comments, shares)
- Store only dirty_posts set (post ID + timestamp)
- Store only leaderboards (sorted sets)
- Total memory: O(posts * categories) for leaderboards, O(posts) for counters
- For 10K posts × 10 categories: ~1MB for leaderboards (reasonable)
- Events remain in PostgreSQL only

### 5. Search vs Ranking Split-Brain

**Problem**: Elasticsearch holds relevance (BM25 on content). Redis holds trending (engagement score). A post could be high-trending but low-relevance or vice versa.

Without reconciliation:
- Search returns by relevance, ignoring trending
- Feed returns by trending, ignoring relevance
- Users see different ranking across endpoints

**Alternative Rejected**: Sync trending scores into Elasticsearch mapping (data duplication, eventual consistency nightmare).

**Final Approach**: 
- Elasticsearch is source of truth for relevance
- Redis is source of truth for trending
- Search endpoint queries ES, then re-ranks results using Redis scores:
  ```
  final_score = 0.7 * es_score + 0.3 * normalized_redis_score
  ```
- Weights are tunable per use case
- Re-ranking happens at query time (acceptable latency since ES returns top-50 only)

### 6. Feed Cache Explosion

**Problem**: Per-user feed cache is infeasible. 1M users × 20 categories = 20M cache keys.

**Alternative Rejected**: 
- Per-user feed cache (memory explosion)
- Bloom filters (false positives break ranking)

**Final Approach**: 
- Cache global and category feeds (1 key per category = 20 keys)
- When leaderboard updates, invalidate category cache
- On first query for a category, rebuild cache from leaderboard
- TTL of 60s as safety net

### 7. Fraud and Engagement Manipulation

**Problem**: Bots and incentivized users can artificially inflate engagement. A post with 500 likes in 1 minute is suspicious.

**Alternative Rejected**: 
- Block high-velocity posts outright (false positives hurt legitimate viral content)
- Complex ML models (maintenance burden)

**Final Approach**: 
- Velocity-based penalty: if event_count / time_window > threshold, apply 0.5x multiplier to score
- Thresholds: >50 events/min = suspicious (0.5x), >200 events/min = flag post (set is_flagged=True)
- Flagged posts excluded from global leaderboard but still appear in category feeds
- Users and support can manually review flagged content

### 8. Debounce Correctness Without Data Loss

**Problem**: If worker crashes mid-cycle, some posts may be double-counted or lost.

**Alternative Rejected**: 
- Transactions (not available for multi-key Redis operations)
- Event sourcing (complexity increase)

**Final Approach**: 
- Worker is idempotent: recomputes score from current counter state, not incremental
- Even if run twice, result is identical
- On crash, post remains in dirty_posts; next cycle processes it
- Worst case: 5s delay in ranking propagation

### 9. Rebuild After Redis Failure

**Problem**: Redis disappears. Rankings go dark. How do we restore?

**Alternative Rejected**: 
- Replicate Redis (doubles complexity, still loses data on partition)
- Read-through cache pattern (adds latency to all requests during outage)

**Final Approach**: 
```
python manage.py rebuild_leaderboards
```

Command:
1. Clears all ranking keys
2. Aggregates EngagementEvent table by (post_id, type)
3. Reconstructs engagement counters
4. Recomputes all scores
5. Rebuilds category and global leaderboards

Takes ~5s for 10K posts. Idempotent (safe to run multiple times).

### 10. Avoiding Silent Misconfiguration

**Problem**: DATABASE_URL set to SQLite or missing SSL mode. System appears to work locally but fails in production.

**Alternative Rejected**: 
- Warnings (developers ignore them)
- Runtime checks (too late; data already corrupted)

**Final Approach**: 
- Settings.py raises ImproperlyConfigured at import time if:
  - DATABASE_URL missing
  - DATABASE_URL resolves to non-PostgreSQL engine
  - SSL mode not set to 'require'
- Test suite enforces these guards
- Cannot start unless configuration is correct

## Architectural Decisions

### Why Neon PostgreSQL Is Source of Truth

**Problem**: Where should the authoritative copy of engagement data live?

**Alternatives**:
1. Redis only (loses data on restart)
2. Elasticsearch only (not optimized for analytical queries)
3. Event log in Kafka (adds infrastructure)

**Decision**: PostgreSQL

**Rationale**:
- EngagementEvent table is append-only, immutable once inserted
- Indexes on (post_id, type, created_at) enable fast rebuilds
- ACID guarantees ensure no double-counting on concurrent likes
- Cost-effective at our scale
- Neon provides failover and snapshots

**Tradeoff**: Must query DB during rebuild (5s latency on full rebuild). Acceptable because rebuilds are rare (usually triggered manually after Redis failure).

### Why Redis Sorted Sets for Leaderboards

**Problem**: Need to efficiently query "top 20 posts in category X by score."

**Alternatives**:
1. PostgreSQL ranking (SELECT ... ORDER BY DESC LIMIT 20 — requires O(posts) scan)
2. Elasticsearch aggregations (slow, not designed for frequent updates)
3. Memcached (no sorted data structure)

**Decision**: Redis sorted sets (ZADD, ZREVRANGE)

**Rationale**:
- O(log N) insert and O(1) top-K retrieval
- Atomic updates via pipelines
- Memory efficient (skip list implementation)
- Native support for merging (ZUNIONSTORE for global merge)

**Tradeoff**: Requires rebuild after failure. Worth it for query performance.

### Why Trending Score Is Not Stored in Database

**Problem**: Should engagement-derived score be in the Post model?

**Alternatives**:
1. Denormalize into posts.trending_score (keep in sync with Redis)
2. Compute on every query (expensive)
3. Cache in Redis only (current approach)

**Decision**: Cache in Redis only

**Rationale**:
- Score changes every 5s; database write volume becomes prohibitive (10M writes/day at scale)
- PostgreSQL is source of truth for engagement events, not scores
- Scores are derived and ephemeral
- If Redis lost, rebuild from events (source of truth)

**Tradeoff**: Cannot query "posts where score > 100" directly from DB. Mitigated because most queries are "top N posts" which Redis handles well.

### Why Debounce Workers Instead of Per-Interaction Scoring

**Problem**: When should score be computed?

**Alternatives**:
1. Synchronously on every like (response latency spike)
2. Asynchronously via Celery task per event (5M tasks/day)
3. Batch debounced worker every 5s (current approach)

**Decision**: Debounced worker

**Rationale**:
- Engagement is bursty; batching amortizes computational cost
- Like endpoint returns immediately (good UX)
- Worker runs on predictable schedule (observable)
- ~60 jobs/day per category instead of 50M events/day

**Tradeoff**: Ranking propagation delayed by up to 5s. Users expect this; trendy posts show new engagement within 5s window.

### Why Global Leaderboard Is Merged Periodically

**Problem**: Global leaderboard is computed from category leaderboards. When to merge?

**Alternatives**:
1. Merge synchronously on every category update (expensive)
2. Merge on query (expensive)
3. Merge every 10s asynchronously (current approach)

**Decision**: Merge every 10s

**Rationale**:
- Merging scans top-50 from each category (O(categories * 50 log posts))
- ~10 merges/minute is acceptable overhead
- Global leaderboard cache invalidated between merges
- Most queries hit category feeds (where leaderboard is current within 5s)

**Tradeoff**: Global feed is stale by up to 10s. Acceptable because global feed is lower priority than category feeds.

### Why Search Re-Ranking Is Done at Query Time

**Problem**: Search index contains content but not engagement scores. How to blend?

**Alternatives**:
1. Sync Redis scores into Elasticsearch mapping (synchronization complexity)
2. Join in application (requires fetching all results)
3. Re-rank at query time from pre-ranked ES results (current approach)

**Decision**: Re-rank at query time

**Rationale**:
- Elasticsearch query returns top-50 by relevance (fast)
- Application fetches Redis scores for those 50 (fast)
- Blends scores: 70% relevance, 30% trending
- No data synchronization required
- Weights adjustable per feature launch

**Tradeoff**: Cannot search for "top trending posts" without relevance filter. Mitigated by separate endpoint (GET /feed/) for pure trending.

### Why Celery Is Configured Eager in Tests

**Problem**: How to test async tasks without introducing timing dependencies?

**Alternatives**:
1. Sleep between engagement and ranking checks (flaky, slow)
2. Mock task execution (doesn't test real behavior)
3. Run tasks eagerly in tests (current approach)

**Decision**: CELERY_TASK_ALWAYS_EAGER = True in tests

**Rationale**:
- Tasks execute synchronously when .delay() is called
- Still hit real Redis and Elasticsearch
- Tests run against real services, no mocks
- Removes timing dependencies
- Can call tasks explicitly in tests (ranking.tasks.recalculate_dirty_scores())

**Tradeoff**: Tests run ~2x slower than mocked tests but catch real bugs (e.g., concurrency issues, service down scenarios).

### Why Real Integration Tests Were Chosen Over Mocks

**Problem**: Should tests use fake Redis/ES/DB or real services?

**Alternatives**:
1. Fakeredis + SQLite (fast, catches logic errors only)
2. Docker containers + real services (slow but comprehensive)
3. Real external services (current approach)

**Decision**: Real services (Neon DB, Redis, Elasticsearch)

**Rationale**:
- Catches race conditions between threads and DB
- Catches serialization issues between services
- Catches connection/timeout handling
- Catches data format incompatibilities (e.g., ES version issues)
- Tests are deterministic (no timing sleep())

**Tradeoff**: Test runtime ~2-3x slower (55 tests in 120s), requires service availability. Mitigated by CI-friendly setup (all services in docker-compose).

## Ranking Algorithm

The trending score is computed from engagement and age:

```
weighted = likes * 3 + comments * 5 + shares * 8
score = weighted / (age_hours ^ 1.5)
```

### Engagement Weights

- **Like: 3x** — low friction, easily triggered
- **Comment: 5x** — indicates deeper engagement
- **Share: 8x** — highest friction, strongest signal

Why this weighting? Empirically, shares correlate with viral reach more than raw engagement count. Comments indicate substantive discussion. Likes are noise by comparison.

### Time Decay Exponent: 1.5

Score decays as time passes. Exponent of 1.5 means:
- After 1 hour: divide by 1.0 (score unchanged)
- After 4 hours: divide by 8.0 (score reduced 87%)
- After 1 day: divide by 27.0 (score reduced 96%)

Why 1.5? Balances:
- Avoid over-weighting recency (exponent too low → today's post beats yesterday's forever)
- Avoid too-fast decay (exponent too high → fresh posts dominate, no long-tail)
- Empirically, 1.5 gives 1-3 day leaderboard half-life

### Velocity-Based Fraud Penalty

After computing score, apply fraud check:

```
if event_count / time_window > threshold:
    score *= 0.5
    if extreme_threshold exceeded:
        is_flagged = True
```

- **Threshold**: 50 events/minute → apply penalty
- **Extreme**: 200 events/minute → flag post and exclude from global

Why this approach? 
- Bots create velocity spikes (10 likes in 1 second vs. 10 likes spread across 1 hour)
- Penalty is proportional; post still ranks but not at top
- Flagged posts don't disappear (support can review)

### Determinism Requirement

The formula must be deterministic:
- Same inputs always produce same output
- No random jitter (tempting but breaks rebuild idempotency)
- No floating-point surprises (use fixed-point when possible)

This ensures `recalculate_dirty_scores()` is idempotent. Running twice produces identical leaderboards.

## System Flow

### Write Path: Engagement to Ranking

```
User likes post
    ↓
POST /posts/{id}/like/
    ↓
engagement_views.LikeView
    ├─ DB: Insert UserPostLike (check uniqueness constraint)
    ├─ DB: Insert EngagementEvent
    ├─ Redis: HINCRBY engagement_hash_key(post_id) "likes" 1
    └─ Redis: ZADD dirty_posts {post_id: time.now()}
    ↓
Response 201 (immediately)
    ↓
[Every 5 seconds]
    ↓
ranking.tasks.recalculate_dirty_scores()
    ├─ Get all post_ids from dirty_posts where timestamp < now - 5s
    ├─ For each post:
    │  ├─ Read engagement counters from Redis HGET
    │  ├─ Fetch post.category from DB
    │  ├─ Compute score (weighting, time decay, fraud check)
    │  └─ Redis: ZADD category_leaderboard {post_id: score}
    └─ Redis: ZREM dirty_posts {post_id}
    ↓
[Every 10 seconds]
    ↓
ranking.tasks.merge_global_leaderboard()
    ├─ For each category leaderboard:
    │  └─ ZREVRANGE top-50
    ├─ Merge in memory (post_id → max score across categories)
    ├─ Filter out flagged posts
    └─ Redis: ZADD global_leaderboard (atomically replace via pipeline)
```

### Feed Path: Query to Render

```
User requests GET /feed/
    ↓
ranking.views.GlobalFeedView
    ├─ Try Redis.get(feed_cache:global)
    ├─ If cache miss:
    │  ├─ Redis: ZREVRANGE global_leaderboard 0 19 (top-20)
    │  ├─ DB: SELECT Post WHERE id IN (...) (fetch metadata)
    │  ├─ Serialize to JSON
    │  └─ Redis: SET feed_cache:global (TTL 60s)
    └─ Return JSON
    ↓
Response 200 (latency: <10ms with cache, <50ms on cache miss)
```

### Search Path: Query + Re-ranking

```
User requests GET /search/?q=python
    ↓
search.views.SearchView
    ├─ Elasticsearch: SEARCH "python" (BM25, limit 50)
    ├─ Extract post_ids and es_scores
    ├─ Redis: HGET global_leaderboard {post_id: score} for each (pipeline)
    ├─ Normalize both score ranges [0, 1]
    ├─ Compute final: 0.7 * es_score + 0.3 * redis_score
    ├─ Sort by final score
    ├─ DB: SELECT Post WHERE id IN (...) (fetch metadata)
    └─ Return JSON
    ↓
Response 200 (latency: 50-100ms depending on ES cluster)
```

### Rebuild Path: Restore from DB

```
Redis failure detected
    ↓
python manage.py rebuild_leaderboards
    ↓
Command handler:
    ├─ Delete all ranking keys from Redis
    ├─ DB: SELECT EngagementEvent (full table scan)
    ├─ Aggregate by (post_id, type): {likes: N, comments: N, shares: N}
    ├─ For each post, compute score from scratch
    ├─ Redis: ZADD category_leaderboard (one pipeline per category)
    ├─ Merge and Redis: ZADD global_leaderboard
    └─ Done (idempotent, safe to repeat)
    ↓
Rankings restored from database truth
```

## Failure Handling Strategy

### Scenario: Redis Completely Lost

**Symptom**: All Redis operations fail with ConnectionError

**Immediate Impact**:
- Engagement endpoints still work (DB writes succeed)
- Feed endpoints fall back to DB (ORDER BY engagement count, slower)
- Search still works (content relevance only, no trending re-ranking)
- Rebuild_leaderboards command begins

**Recovery**:
1. Run `python manage.py rebuild_leaderboards` (takes 5-30s depending on data size)
2. Rankings restored from engagement events in PostgreSQL
3. All functionality returns to normal

**Why This Works**: Engagement events are immutable in DB. Rebuild is idempotent. No data is lost during outage.

### Scenario: Elasticsearch Lag or Unavailability

**Symptom**: Search endpoint returns 503 Service Unavailable

**Immediate Impact**:
- Feed endpoints unaffected (use Redis only)
- Search endpoint fails (cannot fall back without external data)
- Users navigate to trending feed instead

**Recovery**:
1. Elasticsearch recovers or is restarted
2. Index is re-built via `search.tasks.index_post_to_es` (runs on every post creation)
3. Search returns to normal

**Why No Data Loss**: Posts are indexed on creation (Celery eager task). If ES is down during post creation, task is retried. Once ES recovers, all posts are re-indexed (idempotent).

### Scenario: PostgreSQL Connectivity Issues

**Symptom**: 
- Like endpoint returns 500 (DB write fails)
- Rebuild command hangs

**Immediate Impact**:
- Engagement is blocked (this is correct behavior; cannot silently lose data)
- Rankings cannot be updated
- Feeds degrade to cached results only

**Recovery**:
1. Restore PostgreSQL connectivity (fix network, restart DB, etc.)
2. Like endpoint works again immediately
3. Rebuild updates rankings from events that occurred during outage

**Why No Silent Data Loss**: Unlike Redis (ephemeral), PostgreSQL failures are visible and block operations. Forces operator attention.

### Scenario: Debounced Worker Crashes Mid-Cycle

**Symptom**: Worker process dies after reading dirty_posts but before finishing updates

**Immediate Impact**:
- Some posts may or may not be in dirty_posts set (depends on where crash occurred)
- Next worker cycle picks up unprocessed posts
- No double-counting (worker is idempotent)

**Recovery**: Automatic on next cycle (5s later)

**Why This Works**: Worker recomputes scores from current Redis counter state, not from incremental changes. Re-running produces identical result.

### Consistency Model

PulseEngine uses **eventual consistency** with strong guarantees:

- **Strong**: Engagement events are durable in PostgreSQL immediately
- **Eventual**: Ranking reflects engagement within ~5s (debounce) + ~10s (merge)
- **Recoverable**: Full state can be rebuilt from PostgreSQL at any time

This is acceptable because:
- Users expect trending to update over seconds, not milliseconds
- New engagement is visible in post details immediately (DB query)
- Leaderboard catches up within 15s (5s + 10s)

## Testing Strategy

### Why Django Test Runner (Not Pytest)

**Decision**: Use Django's built-in test runner (`python manage.py test`)

**Rationale**:
1. Test database creation is managed by Django (automatic cleanup)
2. Test transactions are automatic (isolation between tests)
3. No additional dependencies (pytest adds maintenance burden)
4. Settings.py guards catch misconfiguration (TEST database uses Neon, not SQLite)

**Tradeoff**: Pytest has better fixtures and parallelization. For this project, Django's simpler model is sufficient.

### Why Real Services in Tests (No Mocks)

**Decision**: All tests use real Neon, Redis, and Elasticsearch

**Rationale**:
1. **Catches concurrency bugs**: Threading issues appear in real DB/Redis, not in mocks
2. **Catches serialization issues**: Real service behavior differs from mocks
3. **Catches integration defects**: Mismatched data formats, version incompatibilities
4. **Deterministic**: No flaky sleeps or race conditions in mock logic
5. **Builds confidence**: If tests pass against real services, deployment is safer

**Examples of bugs caught by real testing**:
- Race condition in UserPostLike.create_user when two threads try to create same like (caught by real DB constraint)
- Redis counter increment ordering with concurrent threads (caught by real Redis)
- Elasticsearch document versioning when re-indexing (caught by real ES)

**Tradeoff**: Tests take 2-3 minutes (55 tests). Worth it for reliability.

### Test Isolation Strategy

Each test runs against isolated:

1. **Database**: Django creates `test_pulseengine` on Neon per run
   - Each test case runs in a transaction (auto-rollback)
   - TransactionTestCase for concurrency tests (no wrapping, real DB locks)

2. **Redis Namespace**: 
   - Each test gets unique prefix: `test:{uuid}:`
   - All ranking keys prefixed with this UUID
   - Teardown scans and deletes only keys in test namespace
   - Never flushes entire Redis (production data safe)

3. **Elasticsearch Index**:
   - Each test creates unique index: `pulseengine_test_{uuid}`
   - ES_INDEX_NAME setting overridden per test
   - Teardown deletes test index only

**Code Example**:

```python
class RealRedisTestCase(TestCase):
    def setUp(self):
        self._redis_ns = f"test:{uuid.uuid4().hex}:"
        # Monkey-patch ranking.constants to use namespace
        rc.engagement_hash_key = lambda pid: self._redis_ns + original(pid)
        
    def tearDown(self):
        # Delete only keys matching test namespace
        self.r.scan(..., match=f"{self._redis_ns}*")
        # Restore originals
        rc.engagement_hash_key = original_func
```

### Removing Timing Dependencies

**Problem**: Original tests used `time.sleep(6)` to wait for debounce worker. Flaky and slow.

**Solution**: Invoke tasks directly in tests

```python
# Before (flaky):
self.r.zadd("dirty_posts", {str(post.pk): time.time() - 10})
time.sleep(6)  # Wait for worker
self.assertEqual(self.r.zscore(...), expected_score)

# After (deterministic):
self.r.zadd("dirty_posts", {str(post.pk): time.time() - 10})
recalculate_dirty_scores()  # Call directly
self.assertEqual(self.r.zscore(...), expected_score)
```

Tests no longer depend on wall-clock time. Run at any speed.

### Concurrency Testing

TransactionTestCase is used for parallel tests:

```python
class TestConcurrentLikes(RealRedisTransactionTestCase):
    def test_100_parallel_likes_no_duplicates(self):
        # Create 100 users
        users = [User.objects.create_user(...) for _ in range(100)]
        
        # Launch 20 parallel threads
        with ThreadPoolExecutor(max_workers=20):
            for user in users:
                executor.submit(like_post, user)
        
        # Verify:
        # 1. All 100 like requests succeeded
        # 2. DB shows exactly 100 UserPostLike records
        # 3. Redis counter shows 100
        # 4. No constraint violations
```

This tests:
- DB uniqueness constraint (first 100 threads win, rest get 409)
- Redis counter increment ordering under contention
- Engagement event creation concurrently

Tests run against real PostgreSQL locks and real Redis pipelines.

### Test Coverage

Current test suite:

- **Database configuration** (11 tests): Neon, SSL, TEST overrides
- **Engagement endpoints** (10 tests): Like, comment, share, duplicates
- **Ranking tasks** (8 tests): Dirty score processing, global merge, idempotency
- **Search** (6 tests): Indexing, reranking, category filter, fraud suppression
- **Integration** (5 tests): End-to-end flow, rebuild command, Redis fallback
- **Concurrency** (3 tests): Parallel likes, duplicate handling, ranking correctness
- **Scoring** (15 tests): Formula verification, fraud penalty, edge cases

**Total**: 55 tests, ~120 seconds on Neon + Redis + Elasticsearch

**What's Not Tested**: 
- Celery Beat scheduling (requires mocking time)
- Elasticsearch connection pooling (would need cluster)
- Large-scale performance (1M posts benchmark)

These are verified manually or in staging.

## Performance Considerations

### Write Path Cost

Single like request:
1. **DB insert EngagementEvent**: ~1ms (sequential write + index update)
2. **DB insert/check UserPostLike**: ~1ms (unique constraint check)
3. **Redis HINCRBY + ZADD**: ~0.1ms (in-memory)
4. **Celery enqueue** (if async): ~0.5ms (negligible, eager in tests)

**Total**: ~2ms per like

At 1000 likes/sec: 2 seconds of database I/O time (parallelizable across 10 threads).

### Leaderboard Update Frequency

Recalculate worker runs every 5 seconds:

- Reads all dirty posts (typically 100-1000 posts)
- Fetches engagement counters (100-1000 Redis GET)
- Computes scores (100-1000 arithmetic operations)
- Updates category leaderboards (100-1000 ZADD)

**Total**: ~50-100ms per cycle (not on critical path)

At 1000 engagements/sec: each post waits 0-5s for ranking update (acceptable).

### Redis Key Growth Control

Keys stored:

```
engagement_hash_key(post_id)       → O(posts) keys
dirty_posts                         → O(posts in last 5s)
category_leaderboard(slug)          → O(posts per category)
global_leaderboard                  → O(posts, max 50 per category)
feed_cache_category(slug)           → O(categories)
feed_cache_global                   → O(1)
```

For 10K posts × 10 categories:
- Engagement hashes: 10K × 50 bytes = 500KB
- Leaderboards: 100K entries × 10 bytes = 1MB
- Dirty posts: ~100 posts × 10 bytes = 1KB
- Cache: ~1KB

**Total**: ~2MB (negligible)

Scaling to 1M posts:
- 50MB (still acceptable on $30/month Redis)

### Why Engagement Events Are Append-Only

EngagementEvent table only grows:

```sql
CREATE TABLE engagement_event (
    id BIGINT PRIMARY KEY,
    post_id INT REFERENCES post,
    user_id INT REFERENCES user,
    type CHAR(10),  -- LIKE, COMMENT, SHARE
    created_at TIMESTAMP,
    INDEX (post_id, type, created_at)
);
```

**Why immutable?**
1. No updates/deletes (disallowed by code)
2. Enables rebuild idempotency (same input → same output)
3. Enables time-series queries (events from date range)
4. Enables audit trail (compliance)

**Scaling consequences:**
- Table grows 50M rows/day at 1M users, 50 events/user/day
- Requires partitioning by date (1 partition/month)
- Index on (post_id, created_at) enables fast rebuild
- But PostgreSQL now becomes bottleneck (need connection pooling, read replicas)

### Scaling Strategy if DAU Increases 100x

**Current bottleneck**: PostgreSQL concurrent connections during rebuild

**Migration path**:
1. **Add read replica**: Rebuild queries point to replica, production writes to primary
2. **Partition engagement_event**: By date or post_id to reduce full-table scans
3. **Introduce Kafka**: Stream engagement events to distributed log (for future consumers)
4. **Replace Celery**: Move ranking to Kafka consumer (stream processor model)
5. **Add search cache**: Reverse index of popular search terms cached in Redis

These are out of scope for current scale (1M DAU). Revisit at 10M DAU.

## What I Would Improve Next

### 1. Move Engagement Events to Kafka

**Benefit**: Decouples engagement ingestion from ranking logic. Enables:
- Replaying events (if ranking algorithm changes)
- Multiple consumers (search indexing, analytics, recommendations)
- Backpressure handling (if ranking falls behind, engage can queue in Kafka)

**Cost**: Additional infrastructure, operational complexity

### 2. Replace Celery Beat with Stream Processor

**Benefit**: Real-time ranking updates instead of 5s debounce.

**Implementation**: Kafka Streams or Flink consumer aggregates engagement events in tumbling windows, emits ranking updates as soon as window closes.

**Cost**: New infrastructure (stream processor cluster), Kafka in critical path

### 3. Add Wilson Score Confidence Ranking

**Current**: Simple weighted score with time decay. Treats 2 likes equally whether from 2 people or same person.

**Better**: Wilson score bounds incorporates confidence. Post with 100 likes and 2 dislikes ranks higher than post with 1 like and 0 dislikes (mathematically sound).

**Cost**: Requires dislike events (feature change), more complex formula

### 4. Rate Limiting and Anti-Bot Heuristics

**Current**: Fraud detection is post-hoc (velocity spike).

**Better**: 
- Per-user rate limits (max 50 engagements/minute)
- Device fingerprinting (same device across accounts)
- Geo-velocity checks (post from USA then Germany in 10 seconds)
- Age-of-account thresholds (accounts < 1 week do less damage)

**Cost**: More complex state tracking, requires separate anti-abuse service

### 5. Add Observability Metrics

**Current**: No metrics logging (only application logs).

**Better**: Prometheus metrics for:
- Engagement rate (events/sec)
- Ranking freshness (max age in leaderboard)
- Redis memory usage
- Elasticsearch indexing lag
- Query latency percentiles

**Cost**: Metrics infrastructure (prometheus, grafana), alerting rules

### 6. Implement Full-Text Search in PostgreSQL

**Current**: Elasticsearch for search (separate system).

**Alternative**: PostgreSQL full-text search (GIN indexes). Reduces systems.

**Cost**: Less flexible relevance tuning, smaller feature set vs Elasticsearch

## Setup Instructions

### Prerequisites

- PostgreSQL 13+ (or Neon account)
- Redis 6+
- Elasticsearch 8.13+
- Python 3.10+
- Docker & docker-compose (for local development)

### Environment Configuration

Copy and configure `.env`:

```bash
cp .env.example .env
```

**Required variables**:

```env
# Django security
SECRET_KEY=your-secret-key-here
DEBUG=False  # True only in development
ALLOWED_HOSTS=localhost,127.0.0.1,your-domain.com

# Database (Neon or local PostgreSQL)
DATABASE_URL=postgresql://user:password@host:5432/pulseengine?sslmode=require

# Services
REDIS_URL=redis://localhost:6379/0
ELASTICSEARCH_URL=http://localhost:9200
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2

# Optional overrides
TEST_DB_NAME=test_pulseengine  # Test database name on PostgreSQL
ES_INDEX_NAME=posts            # Elasticsearch index name
```

**DATABASE_URL Format**:

PostgreSQL on Neon:
```
postgresql://user:password@host.neon.tech:5432/dbname?sslmode=require
```

Local PostgreSQL:
```
postgresql://granth:@localhost/pulseengine
```

**Why SSL required in DATABASE_URL**: Settings.py enforces `sslmode=require` for all databases. Cannot be disabled.

### Docker Startup

```bash
# Start all services (web, Redis, Elasticsearch)
docker-compose up -d

# Or just Redis + Elasticsearch (run Django locally)
docker-compose up -d redis elasticsearch

# Check health
curl http://localhost:8000/health/
# Expected: {"status": "ok"}
```

### Migrations

```bash
# Create test database on Neon
python manage.py migrate

# Or with Docker
docker-compose exec web python manage.py migrate
```

### Running Tests

```bash
# All tests
python manage.py test --verbosity=2

# Specific test module
python manage.py test tests.test_integration

# Specific test
python manage.py test tests.test_integration.TestFullRankingFlow.test_end_to_end_ranking
```

**Test runtime**: ~2-3 minutes on Neon + Redis + Elasticsearch.

### Running Rebuild Command

```bash
# Manually trigger rebuild (after Redis failure)
python manage.py rebuild_leaderboards

# Or with Docker
docker-compose exec web python manage.py rebuild_leaderboards
```

Output:

```
Clearing existing ranking keys...
Rebuilding engagement counters for 1234 posts...
Rebuilding category leaderboards...
Rebuilding global leaderboard...
Done. Rebuilt 12 category leaderboards and global leaderboard with 850 posts.
```

## API Overview

Complete API documentation is in [API_TESTING_GUIDE.md](./API_TESTING_GUIDE.md).

**Primary endpoints**:

- `POST /posts/` — Create a new post
- `POST /posts/{id}/like/` — Like a post
- `POST /posts/{id}/comment/` — Comment on a post
- `POST /posts/{id}/share/` — Share a post
- `GET /feed/` — Global trending feed
- `GET /feed/{category}/` — Category-specific trending feed
- `GET /search/?q=query` — Full-text search with trending re-ranking
- `GET /health/` — Service health check

## Operational Notes

### Monitoring

Watch for:

1. **Redis memory growth**: Should be stable at ~2MB (or scale linearly with posts)
2. **Ranking freshness**: Posts in dirty_posts should not persist >10s
3. **Rebuild time**: Should complete in <60s for 100K posts
4. **Search latency**: P95 should be <100ms

### Alarms

Set alerts for:

- Redis unavailable (fallback to slow DB queries)
- Elasticsearch unavailable (search returns 503)
- Rebuild task hanging (stuck in database scan)
- High engagement velocity (possible DDoS)

### Maintenance

**Weekly**: 
- Monitor Redis memory usage
- Check for orphaned engagement events (posts deleted but events remain)

**Monthly**: 
- Review flagged posts (is_flagged=True) for patterns
- Test rebuild_leaderboards command

**Quarterly**: 
- Re-evaluate fraud thresholds based on user growth
- Profile ranking algorithm (CPU time, memory)
- Analyze search logs for trending query terms
