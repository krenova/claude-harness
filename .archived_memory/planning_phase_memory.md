# Planning Memory

## Iteration 1: Project Blueprint & Phase 1 Planning

**Research Tasks Delegated:** None—iteration 1 was pure planning with design of comprehensive project architecture.

**Key Findings:**
- Designed a self-hosted couples' app (chat, tasks, blog) with multi-tenant group isolation for future scaling
- Identified security requirements: JWT + HttpOnly cookie hybrid auth, WebSocket token ticket pattern for WS auth
- Mapped technology stack: Django + DRF backend, React frontend, PostgreSQL + Redis, Celery for scheduling, Django Channels for WebSockets
- Detailed 6-phase development roadmap with clear KPIs for each phase

**Major Decisions Made:**
1. **Auth Architecture:** Access tokens in memory (15 min JWT), refresh tokens in HttpOnly secure cookies (30 day), with short-lived Redis tickets (30s) for WebSocket authentication
2. **Data Isolation:** All private data (tasks, messages) scoped to `Group`; users can belong to multiple groups with role-based permissions (GROUP_ADMIN vs USER) per group
3. **Real-Time Strategy:** Django Channels + Redis channel layer for WebSocket chat; Celery + Beat for task reminders
4. **Cost Optimization:** iOS as PWA via Web Push (avoid $99 Apple fee); Android as sideloaded Capacitor APK with FCM
5. **Deployment:** Docker Compose with Postgres, Redis, Django (Daphne), Celery worker/beat, Nginx
6. **Frontend:** Single React/Vite codebase serving web, Android (Capacitor), and iOS (PWA via Safari) with `vite-plugin-pwa` using `injectManifest` strategy

**Deliverables Created:**
- `initial_plan.md`: Full project blueprint with tech stack, database schema, and 5-phase dev plan
- `architecture_summary.md`: System architecture diagram, technology matrix, and architectural decision rationale
- `phase_1_plan.md`: Detailed step-by-step tasks for Django foundation (models, JWT auth, admin setup) with KPIs
- Human feedback (iteration 1): please refer to my answers in './plans/human_feedback.md'

---

## Iteration 2: Critical Blocker Resolution & Phase 2 Planning

**Research Tasks Delegated:** Senior staff engineer review of architecture against threat model and implementation plans.

**Key Findings:**
- **5 Critical Blockers Identified:**
  - Push subscription model inconsistency (JSONField vs relational PushDevice; Celery task would crash)
  - Auth rehydration broken (accessing `/me/` before refresh; cold load logs out user)
  - Redis missing password protection (security risk for auth tickets, JWT blacklist, Celery tasks)
  - Firebase service account not mounted in docker-compose (ProgrammingError on startup)
  - Nginx static file serving misconfigured (proxying to Django instead of filesystem volume)
- **7 High-Severity Issues:** Rate limiting missing, CSRF undocumented, task revocation underspecified, WebSocket presence logic missing, production SSL headers missing, migration ordering issues, Android CORS not configured
- **Human feedback resolved:** Self-registration disabled (superuser-only), Celery task tracking with `celery_task_id`, async cache handling, auth rehydration flow, Nginx static serving, Redis auth, Firebase init guard, stale FK handling, Vite proxy for local dev, push notification presence tracking

**Major Decisions Made:**
1. Use relational `PushDevice` model (not JSONField) for multi-device subscriptions
2. Fixed auth flow: `POST /refresh/` → store token → `GET /me/` (not `/me/` first)
3. Added Redis `requirepass` via `REDIS_PASSWORD` environment variable
4. Added Firebase service account volume mount across all services
5. Updated Nginx to use `alias` directive for `/static/` serving from volume
6. Vite proxy (`server.proxy`) handles local dev CORS/SameSite issues natively
7. Redis presence set implementation for suppressing push to connected users

**Deliverables Created:**
- `risk_assessment.md`: Full critical blocker and high-severity issue report with resolutions
- `phase_2_plan.md`: Complete REST API surface (blog, group management, task CRUD, push subscriptions, Celery + Redis setup) with 14 KPI checkboxes
- Human feedback (iteration 2): B1 (User Registration): Only the superuser can add users via the admin page and give the users a default password. The users themselves should then be able to change the password in the settings page.
B2 (Celery Tasks): Yes, add a celery_task_id (CharField, null=True, blank=True) to the todo_task model immediately before initial migrations. When a task is updated or deleted, we will revoke the existing task ID.
B3 (Async Cache): Use Django's native async cache methods (from django.core.cache im

---

## Iteration 3: Real-Time Chat Planning & Harness Refinement

**Research Tasks Delegated:** None—iteration 3 was pure planning and feature design.

**Key Findings:**
- **Phase 3 Architecture Finalized:** Real-time chat via Django Channels + Redis channel layer with persistent PostgreSQL storage; Daphne ASGI serves HTTP and WebSocket on single port
- **WebSocket Auth Pattern Locked:** UUID ticket-based auth (not bearer headers in handshake); client gets 30s-TTL ticket from `POST /api/auth/ws-ticket/`, uses it once in query string, ticket auto-deleted after validation
- **Message Model Simplicity:** Single `Message` table (group, sender, content, created_at) scoped to group; cursor-paginated REST endpoint for history; real-time delivery via channel groups
- **Harness Improvements Identified:** PTY mode for interactive prompt handling, live monitoring phase documentation, rate limiter integration, model utility version consistency

**Major Decisions Made:**
1. **WebSocket Auth:** Ticket pattern chosen over bearer-in-header (WS handshake limitation); prevents token replay and keeps sessions short-lived
2. **Chat Persistence:** Full message persistence with paginated history endpoint; real-time WS layer supplements REST for live updates
3. **Origin Validation:** `AllowedHostsOriginValidator` enforces CORS-like protection at Channels layer; rejects unexpected `Origin` headers
4. **Single App ASGI:** Daphne handles both HTTP (`/api/`) and WS (`/ws/`) on port 8000 via `ProtocolTypeRouter`
5. **Group Isolation:** Messages strictly scoped to group_id; membership check on WS connect prevents cross-group message leakage

**Deliverables Created:**
- `phase_3_plan.md`: 8 detailed implementation sections (Django Channels setup, ASGI config, routing, ticket auth middleware, consumer, model, REST endpoint, validation) with 20+ KPI checkboxes covering ticket auth, WS connection, real-time messaging, chat history, resilience, and Daphne ASGI
- Human feedback (iteration 3): The report is written. Here is my full assessment:

---

## Risk Assessment & Clarifications Report

Acting as Senior Staff Engineer, I performed a cross-phase review of all 6 phase plans against the architecture summary. Here is what I found:

---

### 5 BLOCKING ISSUES (Will cause crashes or confirmed security failures if coded as-written)

B1: Auth Rehydration broken on /me/

Resolution: Update Phase 1/4 authentication logic. On page load, the frontend must first check if an access token exists in memory. If not, it must call the /refresh/ endpoint to obtain a new access token before calling /me/. If /refresh/ fails, redirect to login.

B2: Nginx proxies /static/ instead of serving volume

Resolution: Update the Phase 6 nginx.conf to serve the static files directly from the mounted volume. Remove the proxy_pass block for /static/ and replace it with alias /app/staticfiles/ (or the respective Docker volume mount path).

B3: collectstatic fails at build time

Resolution: Remove collectstatic from the Dockerfile build steps. Create a docker-entrypoint.sh script (or use the command override in docker-compose.yml) that runs python manage.py collectstatic --noinput followed by daphne or gunicorn at container runtime, where the runtime environment variables (like SECRET_KEY) are available.

B4: Firebase JSON missing from Docker & .gitignore

Resolution: I have generated a .firebase_serviceAccountKey.json in the main project folder, you may refer to it. I have also updated .gitignore to ensure this file is not uploaded to any remote repository. In docker-compose.yml, mount this file as a read-only volume into the Django, Celery Worker, and (if applicable) Celery Beat containers: - ./serviceAccountKey.json:/app/.firebase_serviceAccountKey.json:ro.

B5: Redis unprotected without password

Resolution: Update the docker-compose.yml Redis service command to include authentication: command: redis-server --requirepass ${REDIS_PASSWORD}. Pass this REDIS_PASSWORD as an environment variable to the Django and Celery containers so they can form the correct connection string (redis://:password@redis:6379/0).


---

### 6 High-Priority Risks

R1: Capacitor Android CORS

Resolution: (Addressed in Q1). Ensure capacitor://localhost and http://localhost are explicitly included in Django's CORS_ALLOWED_ORIGINS during Phase 1 setup.

R2: Rotating JWT + two open tabs = silent logout

Resolution: In the Phase 4 Axios interceptor, implement a "Refresh Promise Queue." When a 401 occurs, check a local flag (isRefreshing). If true, queue the failing requests until the refresh promise resolves. If false, set it, call /refresh/, update the token, resolve the queue, and seamlessly retry all held requests.


R3: WS presence set missing causing push spam

Resolution: In the Phase 3 Django Channels Consumer, update the connect and disconnect methods to push the user's ID to a Redis Set (e.g., online_users). The Phase 5 Celery push notification task must check if the target user_id is in this set; if they are, suppress the web push notification.

R4: change-password doesn't blacklist current refresh token

Resolution: Update the /change-password/ endpoint logic. Require the client to pass the existing refresh token in the payload. Prior to saving the new password, place the provided refresh token onto the SimpleJWT Blacklist to immediately invalidate the old sessions.

R5: No message length limit in WS consumer

Resolution: Add strict payload validation to the WebSocket consumer. Enforce a maximum character limit (e.g., 100,00 characters) on incoming chat messages. If the limit is exceeded, simply truncate the input and return an warning event to the client.

R6: Cloudflare idle WS 100s timeout

Resolution: (Addressed in Q5). Implement a client-side heartbeat. The frontend frontend should use setInterval to send a minimal {"type": "ping"} string over the WebSocket every 60 seconds. The backend consumer should either ignore it or reply with "pong" to keep the connection active at the Cloudflare edge layer.

---

### 8 Questions Requiring Your Answer Before Coding

1. **What is the deployment hostname/domain?** (Custom domain, Synology DDNS, or LAN IP?) — affects CORS, `ALLOWED_HOSTS`, VAPID claims, and iOS HTTPS requirement.
Answer: Assume a standard custom domain (e.g., ourapp.krenova.com), this should not be hardcoded but easily changeable through a config file. We will configure ALLOWED_HOSTS and CORS_ALLOWED_ORIGINS to include this domain, along with localhost and capacitor://localhost (for Android).
2. **Does a Firebase project already exist?** — it's listed as a Phase 5 prerequisite but requires manual setup.
Answer: No, it does not exist yet. It will be created manually prior to starting Phase 5, and the resulting serviceAccountKey.json will be properly mounted in docker-compose.yml.
Rationale: Acknowledges the prerequisite and addresses the missing volume mount (Blocking Issue B4).
3. **Can both users publish blog posts, or is only one a super-admin?** — determines if `is_super_admin` design is sufficient.
Answer: super-admin is only used to add users, publication of blog posts should be allowed for any user within the same group. this means that each group will have a separate blog post site.
4. **Is the iOS PWA push silent-stop bug acceptable for MVP?** — if not, the WS fallback must be promoted.
Answer: Yes, this limitation is acceptable for the MVP. We will prioritize the WebSocket connection for real-time updates when the app is open.
5. **What Cloudflare plan tier will be used?** — free tier requires WS heartbeat implementation.
Answer: Free tier. We will implement the 60-second WebSocket heartbeat (ping/pong) to prevent idle disconnects.
6. **Should `celery beat` be in the MVP?** — Phase 2 explicitly says it's not needed (ETAs are used). Phase 6 deploys it anyway. Remove or keep?
Answer: Remove celery beat.
Rationale: Since Phase 2 relies on ETAs for future tasks, a dedicated scheduling service is dead weight for the MVP. Removing it saves server resources and simplifies the Docker environment.
7. **What happens to chat messages when a user is deleted?** — currently `CASCADE` (all messages purged). Should it be `SET_NULL`?
Answer: Change to SET_NULL.
Rationale: Using CASCADE destroys the chat history for the remaining user. SET_NULL (often rendered as "Deleted User") preserves the conversation context while safely removing the user record.
8. **Are media file uploads (blog images) in scope for any phase?** — no `MEDIA_ROOT` or media volume exists anywhere in the plans.
Answer: Yes, but we will use a local Docker volume mapped to Django's MEDIA_ROOT, not PostgreSQL or S3.
- Human feedback (iteration 4): there is no information in the risk document.

---

## Iteration 5: Cross-Phase Architecture Review & Critical Issue Identification

**Research Tasks Delegated:** Senior staff engineer cross-phase review of all 6 phase plans against architecture summary to identify architectural inconsistencies, security gaps, and implementation defects.

**Key Findings:**
- **4 Critical Issues Identified:**
  - C1: `change-password` endpoint design flaw (cannot access HttpOnly refresh token from JavaScript)
  - C2: `Message.sender` uses `CASCADE` instead of confirmed `SET_NULL` (loses chat history on user deletion)
  - C3: WS presence set never written by consumer despite being read by Celery task reminders (push suppression broken)
  - C4: Phase 6 KPIs reference removed `celery_beat` service (causes deployment confusion)
- **7 High-Priority Issues:** Blog admin permission mismatch, missing SSL proxy headers, WebSocket heartbeat not implemented in Phase 4, message length cap missing from Phase 3, Firebase initialization not guarded, XSS vector in blog (react-markdown raw HTML), rate limiting absent from auth endpoints
- **7 Architectural Questions:** change-password mechanism, Django admin exposure strategy, message character limit, WS presence key TTL handling, blog admin UI permission model, SSL redirect delegation, Celery task durability on crash

**Major Decisions Made:**
- Escalation of all 4 critical issues to blocking status; requires human answers to 7 design questions before Phase 4-6 implementation proceeds
- Confirmed that Phase 5+ implementation is blocked pending resolution of C1-C4 and high-priority architectural decisions
- Planning state transitioned to `in_progress` with iteration counter set to 5
- Human feedback (iteration 5): Here are recommended solutions for the critical and high-priority issues, along with answers to the 7 questions to help you refine your plans.

### Critical Issues Solutions
*   **C1 (`change-password` impossible):** Refactor the endpoint to extract the `refresh_token` from the `HttpOnly` cookie on the server side (`request.COOKIES.get('refresh_token')`). Do not expect it in the JSON payload.
*   **C2 (`Message.sender` CASCADE):** Update the `Message` model definition to use `on_delete=models.SET_NULL, null=True` for the `sender` foreign key.
*   **C3 (WS Presence missing):** In `ChatConsumer.connect()`, add logic to set the Redis key (`ws_presence:{user.id}`). In `disconnect()`, delete the key.
*   **C4 (`celery_beat` referenced):** Remove all references to `celery_beat` from Phase 6 KPIs and any deployment checklists to align with the chosen architecture.

### High-Priority Issues Solutions
*   **H1 (Blog admin UI gate mismatch):** Update the frontend routing logic to evaluate group membership or authentication status, matching whatever the backend requires, rather than checking `is_super_admin`.
*   **H2 (`SECURE_PROXY_SSL_HEADER` missing):** If keeping SSL redirect in Django, add `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')` to `settings.py`. Alternatively, see Q6 below.
*   **H3 (WS Heartbeat missing):** Add an application-level heartbeat in the React frontend (e.g., via `setInterval` sending a `{"type": "ping"}` every ~30 seconds) to prevent Cloudflare from dropping idle WebSockets.
*   **H4 (Message length cap missing):** Add validation in `ChatConsumer.receive()` to immediately reject or truncate payloads exceeding the defined maximum length.
*   **H5 (Firebase initialize guard):** Wrap the initialization in a check: `if not firebase_admin._apps: firebase_admin.initialize_app()`.
*   **H6 (Stored XSS vector):** Integrate `rehype-sanitize` with `react-markdown` to safely strip raw HTML from blog content before rendering.
*   **H7 (No rate limiting):** Implement basic throttling (e.g., DRF's `AnonRateThrottle` and `UserRateThrottle`) on `/login/`, `/register/`, and `/change-password/`.

---

### Suggested Answers for the 7 Questions

1.  **`change-password` design:** **Read from HttpOnly cookie.** It is the most secure and standard approach since the client cannot (and should not) access it.
2.  **Django Admin exposure:** **IP-restricted.** Since this is an MVP, restricting the `/admin/` route via Nginx or Cloudflare WAF rules to known developer IP addresses is a low-effort, high-security win.
3.  **Chat message character limit:** **Confirm 10,000 characters.** This is generous for standard chat but safely bounds memory usage.
4.  **WS Presence key lifetime:** **TTL refreshed by heartbeat.** Relying purely on `disconnect()` leaves orphan keys if the server crashes or the connection drops ungracefully. Set a 60s TTL and refresh it with the websocket heartbeat (H3).
5.  **Blog Admin UI gate:** **Require "belongs to a group".** This keeps the UI completely aligned with the backend permission model without over-engineering roles.
6.  **`SECURE_SSL_REDIRECT`:** **Delegate entirely to Cloudflare.** Turn it off in Django and set Cloudflare's SSL/TLS encryption mode to "Full" or "Full (Strict)" and enable "Always Use HTTPS" in Cloudflare edge settings. This prevents proxy infinite-redirect headaches.
7.  **Celery task durability on crash:** **Accept default lost-task behavior.** For a 2-user MVP, `acks_late=True` adds unnecessary complexity. Tasks like push notifications can occasionally be dropped in an early MVP without catastrophic failure.
- Human feedback (iteration 6): please carry on.

---

## Iteration 7: Redis Cache Gap Discovery & Consistency Fixes

**Research Tasks Delegated:** Cross-phase review to verify all phases can execute as written, focusing on Django cache usage across WebSocket auth, presence tracking, and task scheduling.

**Key Findings:**
- **Critical Gap:** Phase 1 never configures `CACHES['default']` with Redis backend. Django defaults to `LocMemCache`, which would cause all WS ticket validations to fail and presence keys to be per-process (push notifications fire to everyone)
- **Phase 2/3 Inconsistency:** Phase 2 prose says "Redis Set" for presence, but Phase 3 implementation uses key-value with TTL
- **WS Ticket TTL Too Tight:** 30s window creates edge cases for slow clients; 120s is more practical without compromising security
- **3 Open Questions:** Slug uniqueness scope (global vs per-group), PushDevice schema redundancy (individual columns vs JSONField), celery_beat flag documentation

**Major Decisions Made:**
1. **Add Redis Caches Config:** Insert 4-line `CACHES['default']` block into Phase 1 `settings.py` using `REDIS_URL` env var before any downstream phases
2. **Extend WS Ticket TTL:** Change from 30s to 120s for legitimate client headroom
3. **Clarify Phase 2 Prose:** Fix narrative to reference key-value TTL pattern, not Redis Set
4. **Phase Plans Updated:** phase_3_plan.md reflects all H3-H7 fixes (heartbeat, presence TTL refresh, message length cap, presence in connect/disconnect)
- Human feedback (iteration 7): Here are the recommended solutions to address the high-priority risks and answers to your open questions to help finalize the plans:
- Human feedback (iteration 8): Here are the proposed responses and solutions to address the critical gap, high-priority risks, and open questions in your risk assessment:

### Proposed Fix for the Critical Gap
**Redis Cache Missing in Phase 1:**
You must update the Phase 1 plan to explicitly include the caching infrastructure. Add the following to Phase 1, Step 1:
1.  **Dependency:** Add `django-redis` to the `pip install` command (or requirements.txt).
2.  **Configuration:** Add the following block to `settings.py`:
    ```python
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": env("REDIS_URL", default="redis://127.0.0.1:6379/1"),
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            }
        }
    }
    ```

### Proposed Fixes for High-Priority Risks
*   **HP-2 (Hardcoded `GROUP_ADMIN`):** Define a constant in a centralized location:
Ideally inside the relevant models.py (Recommended). The absolute best place for a role constant is right next to the model that uses it. You can define it at the top of the file, or better yet, as a Django TextChoices class if it's used as a database field:
```python
# users/models.py
from django.db import models

class Role(models.TextChoices):
    GROUP_ADMIN = 'GROUP_ADMIN', 'Group Admin'
    MEMBER = 'MEMBER', 'Member'
# Usage elsewhere:
# from users.models import Role
# if user.role == Role.GROUP_ADMIN:
```
If you aren't using choices, just put ROLE_GROUP_ADMIN = 'GROUP_ADMIN' right below the imports in models.py.


*   **HP-3 (Celery `__init__.py` guard):** Update the Phase 2 plan to explicitly include the creation/modification of `backend/__init__.py` with the following lines to ensure Celery loads when Django starts:
    ```python
    from .celery import app as celery_app
    __all__ = ('celery_app',)
    ```
*   **HP-4 (`drf-nested-routers` missing):** Update the installation step in Phase 2 to explicitly include `pip install drf-nested-routers`.

### Proposed Answers to Open Questions
*   **Answer to Q1 (PushDevice schema):** **Yes, simplify the schema.** Using a single `subscription_json` (JSONField) + `fcm_token` (CharField) is much better. It heavily simplifies the model, perfectly aligns with Phase 5's query logic (`subscription_json__endpoint`), and prevents you from having to run database migrations if the Web Push standard ever adds new fields.
*   **Answer to Q2 (Celery concurrency):** **Keep it simple; `-c 2` is sufficient for the MVP.** For a 2-user application, introducing queue isolation (`-Q celery,chat_push`) adds unnecessary deployment complexity. Two concurrent workers are more than enough to handle basic background tasks and pushes without blocking for this scale. Document that queue routing can be considered as a post-MVP scaling optimization.

---

## Iteration 9: Cross-Phase Consistency Verification

**Research Tasks Delegated:** Senior staff engineer cross-phase consistency review comparing all phase plans against each other to identify contradictions, missing dependencies, and configuration drift.

**Key Findings:**
- **2 Critical Gaps Identified:**
  - C1: Redis database number contradicts across phases (Phase 1/3 use `/1`, Phase 2/6 use `/0`) — would cause Celery broker keys to collide with Django cache or channel layer
  - C2: `token_blacklist` missing from Phase 1 `INSTALLED_APPS` despite `BLACKLIST_AFTER_ROTATION=True — logout and change-password endpoints would crash
- **4 High-Priority Risks:**
  - HP-1: Phase 1 PushDevice uses separate columns; Phase 5 filters on `subscription_json` (doesn't exist)
  - HP-2: `drf-nested-routers` referenced but missing from Phase 2 pip install
  - HP-3: `'GROUP_ADMIN'` hardcoded as string in every phase — typo silently returns zero results
  - HP-4: `send_task_reminder` KPI doesn't verify it sends only to the assignee

**Major Decisions Made:**
1. **Redis Layout:** Requires two variables (`REDIS_URL` for Celery, `REDIS_URL_CACHE` for Django/Channels) to prevent DB collisions
2. **Must Add token_blacklist:** Add `'rest_framework_simplejwt.token_blacklist'` to Phase 1 INSTALLED_APPS before Phase 1 execution
3. **PushDevice Schema Locked:** Phase 1 plan updated with single `subscription_json` JSONField + `fcm_token` (simplified, flexible)
4. **3 Open Questions for Human Feedback:** Redis layout approach, PushDevice schema finalization, Celery concurrency scaling
- Human feedback (iteration 9): Here are the proposed solutions and answers for this updated iteration of the risk assessment:

### Solutions for Critical Gaps

*   **C1 (Redis DB Contradictions):** This is resolved by answering Q1 below. Using separate database numbers is the correct approach to prevent key collisions between Celery and Django's cache/Channels.
*   **C2 (`token_blacklist` missing):** Update the Phase 1 setup plan. In the `settings.py` configuration step, explicitly add `'rest_framework_simplejwt.token_blacklist'` to the `INSTALLED_APPS` list to satisfy the `BLACKLIST_AFTER_ROTATION: True` requirement.

### Solutions for High-Priority Risks

*   **HP-1 (`PushDevice` schema mismatch):** This is resolved by answering Q2 below (switching to Option A).
*   **HP-2 (`drf-nested-routers` missing):** Update Phase 2's dependency installation step to explicitly include `pip install drf-nested-routers`.
*   **HP-3 (Hardcoded `GROUP_ADMIN`):** Define a constant inside your User or Group `models.py` (e.g., `class Role(models.TextChoices): GROUP_ADMIN = 'GROUP_ADMIN', 'Group Admin'`) and update the plans to import and use this constant instead of the raw string.
*   **HP-4 (Reminder target KPI):** Update the acceptance criteria/KPIs for the task reminder feature to explicitly state: *"Verify that `send_task_reminder` only dispatches WebSocket/Push events to the specific user ID in `assigned_to`, not all members of the group."* Add a unit test verifying this exact behavior.

---

### Answers to the 3 Questions

**Q1 — Redis layout:** 
**Answer: Use two variables.** 
It is highly recommended to logically separate your cache from your message broker to prevent accidental key evictions or collisions. Define `REDIS_URL` (defaulting to `redis://.../0`) for Celery/Broker and `REDIS_URL_CACHE` (defaulting to `redis://.../1`) for Django Cache & Channels.

**Q2 — PushDevice schema:** 
**Answer: Option A (Single `subscription_json` JSONField).** 
This is the standard, modern way to store Web Push subscriptions. It handles future standard changes automatically, keeps the database schema clean, and perfectly aligns with the Phase 5 cleanup filter (`subscription_json__endpoint`). Just make sure to also include an `fcm_token` `CharField` if you still plan to support mobile Firebase pushes.

**Q3 — Celery concurrency:**
**Answer: Keep `-c 2` for MVP, but add a documentation note about queue isolation.**
For a 2-user MVP, two generic worker processes are completely fine. However, it’s a good idea to add a comment in the architecture docs noting that *"For future scaling, we should implement queue isolation (e.g., `-Q celery,chat_push`) to prevent generic background tasks from delaying real-time push notifications."*

---

## Iteration 10: Redis Config Inconsistency & Signal Import Fixes

**Research Tasks Delegated:** Worker agent analyzed Redis configuration consistency across all phase plans.

**Key Findings:**
- **Critical Gap:** Phase 3’s `CHANNEL_LAYERS` uses `REDIS_URL` (DB/0) instead of `REDIS_URL_CACHE` (DB/1), causing Celery and Channels to collide on same Redis database
- **Missing Config:** Phase 6 docker-compose never defines `REDIS_URL_CACHE`, so the intended separation never takes effect
- **Signal Import Crash:** Phase 3’s `chat/signals.py` imports `send_chat_push` from Phase 5’s `tasks.py` — Phase 3 cannot be tested standalone
- **Nginx Build Context:** Phase 6 Dockerfile multi-stage build has wrong `build: context:` pointing to wrong directory

**Major Decisions Made:**
1. Phase 3: Change `env(‘REDIS_URL’` → `env(‘REDIS_URL_CACHE’` on line 30
2. Phase 6: Add `REDIS_URL_CACHE: redis://:${REDIS_PASSWORD}@redis:6379/1` to django and celery_worker services
3. Prior risk assessment issues (Redis contradiction, missing token_blacklist, PushDevice schema) confirmed resolved
4. 5 Open Questions pending: signal stub strategy, nginx build approach, onboarding flow, celery retry policy, push notification defaults
- Human feedback (iteration 10): Here are the recommended solutions to the critical gaps, moderate risks, and open questions to help you refine your plans for the 2-user MVP.

### Proposed Answers to the 5 Open Questions

1. **Q1 — Signal stub strategy: Option C (Phase 3 no-op task).** 
   Define a placeholder `send_chat_push` Celery task (`@shared_task def send_chat_push(*args, **kwargs): pass`) in Phase 3. This resolves **C1** by allowing the code to compile and be tested in isolation, and it can simply be overwritten with the real logic in Phase 5.
2. **Q2 — Nginx/frontend Docker build: Option A (Single Dockerfile context at repo root).** 
   Set the build `context: .` in your `docker-compose.yml` for the Nginx service. This resolves **C2** by allowing a single multi-stage `Dockerfile` to read the frontend source, build the React static files, and copy them into the final Nginx image without the fragility of named volume syncing.
3. **Q3 — First-time onboarding: SSH + `createsuperuser`.**
   For a 2-user MVP, building, testing, and securing a dedicated `/bootstrap/` endpoint is unnecessary overhead. Create the initial users via the Django CLI.
4. **Q4 — Celery retry policy: Log-and-drop.**
   Do not build a dead-letter queue for this MVP. If a push notification or minor background task fails after max retries, logging the error and dropping the task is perfectly acceptable.
5. **Q5 — Push notification priority/sound:**
   Set both to "default" priority with the default system sound. Custom notification channels/sounds add mobile OS-specific complexity that is better left for a v2 polish.

---

### Proposed Solutions for the Moderate Risks (MR)

*   **MR-1 (Sanitization):** Since this is an MVP, enforce text-only chat messages initially. If rich text is ever needed, ensure the frontend treats payloads as plain text strings (e.g., standard React state rendering, never dangerously setting inner HTML) rather than adding complex backend sanitization libraries right now.
*   **MR-2 (PushDevice orphans):** Accept this risk for the MVP. With only 2 users, orphaned DB rows will consume negligible space. You can manually prune them via Django Admin if needed.
*   **MR-3 (Rate Limiting mismatch):** Adjust the `change-password` rate limit in your DRF throttling configuration to match (or be stricter than) the `login` limit, e.g., 5 to 20 requests per hour.
*   **MR-4 (Missing leave/invite flow):** Explicitly document this as "Out of Scope" for the MVP. Administration of the 2 users should be handled strictly via the Django Admin panel. 
*   **MR-5 (Group.name uniqueness):** Update the Phase 1 schema plan to add `unique=True` to the `Group.name` field.
*   **MR-6 (initial_plan.md contradictions):** Update `initial_plan.md` to deprecate `is_super_admin` (in favor of the group role approach) to maintain a single source of truth across all documentation.

---

## Iteration 11: Pre-Execution Final Review

**Research Tasks Delegated:** Senior staff engineer pre-execution review of all phase plans and implementation artifacts to identify blocking issues before Phase 1 execution begins.

**Key Findings:**
- **5 Critical Blockers identified:**
  - C1: `REDIS_URL_CACHE` missing from `docker-compose.yml` django service — Django falls back to LocMemCache, breaking WS tickets, presence, and Channels in Docker
  - C2: Phase 2 push notification stub functions have wrong call signatures (`send_webpush_notification(device, task)` vs `send_webpush_notification(subscription_info, **payload)`)
  - C3: `send_task_reminder` has no `max_retries` — infinite retry potential on FCM failures
  - C4: `django-celery-beat` in `requirements.txt` but beat service removed — dead weight
  - C5: Nginx Dockerfile is empty placeholder stub — `dist/` missing after build
- **6 High-Priority Risks:** WS ticket TTL contradiction (120s vs 30s), `IsGroupAdmin` missing `has_permission`, task reminder KPI gap, Blog Admin UI gate mismatch (`is_super_admin` vs `GROUP_ADMIN`), `/admin/` IP restriction missing, `change-password` throttle undefined rate
- **3 Open Questions:** Nginx Dockerfile approach (single root vs split), superuser creation method, frontend build strategy (CI pre-build vs Docker bake)

**Major Decisions Made:**
- All issues from iterations 1-10 confirmed resolved
- 3 Open Questions pending human feedback: nginx build approach, initial superuser creation, frontend CI strategy
- Planning state: `in_progress`, awaiting human answers before Phase 1 execution
- Human feedback (iteration 11): r
- Human feedback (iteration 11): Here are the proposed solutions and answers for this final Pre-Execution Risk Assessment. You can use these to finalize the plans before coding begins.

### Answers to the 3 Open Questions

**Q1 — Nginx Dockerfile strategy:**
**Answer: Yes, confirm Option A.** A single multi-stage `nginx/Dockerfile` executed from the repository root is the cleanest approach. It allows Docker to pull the frontend source code, build the React static files, and copy them into the final Nginx image—all in one isolated step. Use `ARG VITE_*` to inject necessary environment variables during the frontend build stage.

**Q2 — Initial superuser creation:**
**Answer: Yes, confirm.** For this 2-user MVP, running `docker compose exec django python manage.py createsuperuser` after deployment is the safest, most efficient onboarding strategy. It avoids the security overhead of building a public registration or bootstrap endpoint.

**Q3 — Frontend build execution (Local vs. Docker):**
**Answer: Bake `VITE_*` args into the Docker build.** Building inside Docker ensures the build is completely reproducible and does not depend on the local developer's Node version. Pass the variables in your `docker-compose.yml` build block using `args:`.

---

### Solutions for the 5 Critical Blockers

*   **C1 (`REDIS_URL_CACHE` missing):** Update the Phase 6 `docker-compose.yml` plan. Add `REDIS_URL_CACHE: redis://redis:6379/1` to the `environment` block of the `django` and `celery_worker` services so Channels and cache don't silently degrade to local memory.
*   **C2 (Push notification stub signature mismatch):** Update the Phase 2 plan to define the stub as `def send_webpush_notification(subscription_info, **payload): pass`. This ensures Phase 3 and Phase 5 can successfully import and call it without throwing a `KeyError`.
*   **C3 (`send_task_reminder` missing `max_retries`):** Update the Phase 5 plan to add `bind=True, max_retries=3` (or similar) to the `@shared_task` decorator for the task reminder to prevent infinite retry loops.
*   **C4 (`django-celery-beat` in requirements):** Remove `django-celery-beat` from the requirements.txt list in the Phase 1/Phase 2 instructions, as periodic tasks were explicitly removed from the architecture.
*   **C5 (Nginx Dockerfile empty):** This is resolved by the answer to Q1. Update Phase 6 to include the full multi-stage Dockerfile instructions (e.g., `FROM node:18 AS builder ... copy package.json ... run build ... FROM nginx:alpine ... COPY --from=builder /app/dist /usr/share/nginx/html`).

---

### Solutions for the 6 High-Priority Risks

*   **H1 (WS ticket TTL mismatch):** Standardize strictly on **120s**. Update the Phase 3 plan and its KPIs to test for a 120-second expiration.
*   **H2 (`IsGroupAdmin` missing `has_permission`):** Update the Phase 2 DRF permissions plan to explicitly implement *both* `has_permission()` (to block collection-level access) and `has_object_permission()` (to block object-level access).
*   **H3 (Phase 5 KPI missing real delivery check):** Update the Phase 5 KPI to state: *"Verify a push notification payload is successfully received by a connected frontend client or test script, not just dispatched by the backend."*
*   **H4 (Phase 4 Admin UI gate contradicts backend):** Update Phase 4 to check the user's role (e.g., `user.role === 'GROUP_ADMIN'`) instead of `is_super_admin` to match the backend permission model.
*   **H5 (`/admin/` lacks IP restriction):** Update the Nginx configuration plan in Phase 6 to include an `allow [Trusted-IP]; deny all;` block for the `location /admin/` block.
*   **H6 (`change-password` throttle undefined):** Update the `REST_FRAMEWORK` settings block in Phase 1/2 to explicitly include `'change_password': '10/hour'` within the `DEFAULT_THROTTLE_RATES` dictionary.

---

## Iteration 12: Single-Agent Execution Modes

**Research Tasks Delegated:** None — iteration 12 refactored the harness to support single-agent mode for users running with `--n-sub-agents=1`.

**Key Findings:**
- Both plan_refinement and execution phases required separate delegation, research, and execution steps incompatible with single-agent mode
- Single-agent mode needs combined prompts merging task planning with execution/research
- Unused `run_orchestrator` import in `plan_refinement.py` remained after earlier refactor

**Major Decisions Made:**
1. **Plan Refinement (single-agent):** Added `single_agent_iter1` and `single_agent_iter_n` prompts combining delegation + research + plan generation into one orchestrator call
2. **Execution (single-agent):** Added `single_agent_loop1` and `single_agent_loop_n` prompts combining task planning + worker execution into one call with no JSON parsing
3. **Removed unused import:** Cleaned up stale `run_orchestrator` import from `plan_refinement.py`

**Files Modified:** `plan_refinement.yaml` (+44 lines), `execution.yaml` (+21 lines), `plan_refinement.py` and `execution.py` (refactored with single-agent branches)

---
- Human feedback (iteration 12): Here are the recommended answers and decisions for these final pre-execution items, keeping in mind the scale and goals of your 2-user MVP:

---

## Iteration 13: Execution Single-Agent Mode & Unattended Feedback

**Research Tasks Delegated:** None — iteration 13 was implementation work building on iteration 12's single-agent prompts.

**Key Findings:**
- Single-agent execution mode needed combined prompts (task planning + execution) without JSON parsing or worker delegation
- Loop iteration feedback needed KPI status tracking to focus remaining work
- Plan refinement needed agent-generated approval mechanism for unattended operation (no human input)

**Major Decisions Made:**
1. **Execution prompts:** Added `single_agent_loop1` and `single_agent_loop_n` prompts to `execution.yaml` combining task execution with KPI validation in one orchestrator call
2. **Execution branching:** Refactored `execution.py` to branch on `cfg.n_sub_agents == 1`, skipping delegation/worker pattern for single-agent mode
3. **Unattended feedback:** Added `unattended_feedback` prompt to `plan_refinement.yaml` for agent-generated approval when running without human input
4. **Unattended mode:** Added `cfg.unattended_mode` handling in `plan_refinement.py` to generate planning feedback autonomously

**Files Modified:**
- `execution.yaml` (+23 lines): single-agent loop prompts
- `execution.py` (+90 lines): single-agent branching logic
- `plan_refinement.yaml` (+8 lines): unattended feedback prompt
- `plan_refinement.py` (+14 lines): unattended mode support

---

- Human feedback (iteration 12): Here are the recommended answers and decisions for these final pre-execution items, keeping in mind the scale and goals of your 2-user MVP:
- Human feedback (iteration 13): Here are the recommended answers and decisions for these final pre-execution items, tailored to keep the 2-user MVP moving quickly and securely:

### Answers to Critical Decisions

**A1 (R-1): Archiving `initial_plan.md`**
**Decision: Yes, archive it.** 
Move it to an `.archive/` or `deprecated/` folder. The Phase 1-6 plans are your definitive source of truth now; keeping the outdated initial plan around will only cause confusion and hallucination risks during development.

**Q1 (R-2): `ws_presence` TTL edge case**
**Decision: Accept the 60-second window.** 
For a 2-user app, the rare event of someone losing network, instantly opening the app on a second device, and receiving a duplicate chat notification within a 60s window is harmless. Building client-side deduplication logic is a waste of MVP budget.

**Q2 (R-3): Admin IP Allowlist**
**Decision: Replace hardcoded IPs with Cloudflare Access (Zero Trust).** 
Instead of hardcoding residential IPs (which change dynamically and break Nginx configurations), remove the IP restrictions from Nginx entirely and secure the `/admin/` route using Cloudflare Access. It's free, takes 5 minutes to set up, and requires an email OTP or Google Auth to access the route.

---

### Answers to Medium/Open Questions

**Q3 (R-5): Cloudflare vs. Let's Encrypt**
**Decision: Cloudflare.** 
Since your architecture already relies heavily on Cloudflare (for SSL termination, proxying, and WebSocket keepalives), you should configure Nginx to use Cloudflare Origin Certificates and unconditionally trust the `CF-Connecting-IP` header as the real client IP.

**Q4: Firebase project ownership**
**Answer: You (the developer) own it.** 
You will create the Firebase project under your personal Google account, generate the `serviceAccountKey.json`, and inject it into the production environment via secure environment variables or a tightly controlled mounted volume.

**Q5: VAPID `mailto:` email address**
**Answer: Provide a dummy or developer email.** 
Use something like `admin@yourdomain.com` or your personal developer email. Push providers (like Mozilla/Google) only use this to contact you if your integration is severely misbehaving or spamming requests.

**Q6: Android APK distribution (sideload method)**
**Answer: Google Drive or direct download link.**
For 2 users, simply build the signed APK or AAB locally, upload it to a shared Google Drive folder, and download/install it directly on the target Android devices. There is zero need to deal with Google Play Store review processes for this MVP.

---

## Iteration 14: Single-Agent Mode Enhancements & AUTONOMOUS_MODE

**Research Tasks Delegated:** None — iteration 14 was implementation work enhancing the harness single-agent capabilities and renaming terminology.

**Key Findings:**
- Single-agent execution prompts needed more detailed execution steps and KPI tracking guidance
- "Unattended" terminology was confusing — renamed to "AUTONOMOUS_MODE" for clarity
- Rate limiter had unused text patterns that could cause false positives (e.g., "5-hour" narrative text)

**Major Decisions Made:**
1. **Execution prompts:** Enhanced `single_agent_loop1` and `single_agent_loop_n` with task execution steps and KPI tracking integration
2. **Unattended feedback:** Added `unattended_feedback` prompt to both `execution.yaml` and `plan_refinement.yaml` for agent-generated guidance when no human input
3. **AUTONOMOUS_MODE rename:** Renamed `UNATTENDED_MODE` to `AUTONOMOUS_MODE` across config.py, main.py, helpers.py, rate_limiter.py, execution.py, plan_refinement.py, and tests
4. **Rate limiter cleanup:** Commented out unused text patterns ("rate limit", "overloaded", "usage limit") that produced false positives on narrative content

**Files Modified:**
- `src/prompts/workflows/execution.yaml` (+26 lines): detailed execution prompts with KPIs
- `src/prompts/workflows/plan_refinement.yaml` (+8 lines): unattended feedback prompt
- `src/workflows/execution.py` (+140 lines): enhanced single-agent branching with detailed steps
- `src/workflows/plan_refinement.py` (+29 lines): AUTONOMOUS_MODE support
- `config.py`, `main.py`, `src/helpers.py`, `src/safeguards/rate_limiter.py`, `tests/test_integration.py`: renamed flag

---
