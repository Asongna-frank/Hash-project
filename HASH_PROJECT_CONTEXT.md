# HASH Project — Full Context for AI Assistants

> **How to use this file:** Drop it in the root of the project directory.
> Reference it in every Copilot Agent prompt with:
> "Read HASH_PROJECT_CONTEXT.md before writing any code."
> Update this file at the end of every sprint.

---

## 1. What HASH Is

HASH is a maternal care and early pregnancy loss support platform for
sub-Saharan African healthcare settings. It connects pregnant women with their
assigned hospitals through an AI chatbot and a clinical dashboard.

Two patient populations:
- **Smartphone patients** — mobile/web app: chat, tips, EDD countdown, emergency GPS button.
- **Choronko (GSM/SMS) patients** — feature phone only. Clinician registers them
  at the hospital using phone number as sole identifier. Full care delivered via SMS.

Neither track is second-class. Every feature must work on both. All content
needs a full form (in-app) and an SMS-safe short form.

---

## 2. The Two Core Concepts — Never Conflate

### Patient Risk Level
Computed automatically at signup from weighted questionnaire answers.
Controls proactive check-in frequency:
- **High** → every 3 days
- **Medium** → weekly (every 7 days)
- **Low** → fortnightly (every 14 days)

(High is set to ~2x the frequency of Medium; 7÷2 = 3.5 days, rounded down to 3
to err toward more contact. Pending Dr Elvira's confirmation that every-3-days
is acceptable for High rather than daily.)

Can be manually overridden by a clinician. Changes logged. Takes effect within
5 minutes. Stored on the patient record with full audit trail.

### Message Acuity
Assigned in real time to every individual message the patient sends
(Low / Medium / High). Controls bot response and whether to alert the hospital.
Does NOT change the patient's risk level. Computed by M4 triage engine.

**These are completely independent.** Never merge them in code or naming.

---

## 3. Technology Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend framework | FastAPI (Python) | Currently in development on local PC |
| Database | PostgreSQL via Supabase | Already set up and in use |
| ORM | SQLAlchemy | With Alembic for migrations |
| Auth | python-jose (JWT) + passlib (bcrypt) | Implemented in M1 |
| LLM (local dev) | Groq API | Free, fast. Model: llama3-8b-8192 |
| LLM (production) | Amazon Bedrock | Future — when AWS is set up |
| NLP assist (future) | Amazon Comprehend | Future — keyword layer covers MVP |
| SMS (choronko) | Queen SMS | **In use now.** Cameroon provider. `QUEEN_SMS_API_KEY` in `.env` |
| Push notifications | In-app message + poll flag | MVP: in-chat message + `is_read` poll flag. FCM is Phase 2 |
| Notifications (future cloud) | Amazon SNS / SES | Future — push + email when AWS is set up |
| Infrastructure | AWS (future) | ECS Fargate, Aurora, Redis, S3, SNS, SES |
| CI/CD | GitHub Actions + AWS CodeBuild | Future |

### LLM Provider Abstraction (critical design rule)
All LLM calls go through a single `LLMService` interface in
`app/services/llm_service.py`. The active provider is controlled by
`LLM_PROVIDER` in `.env`. Setting `LLM_PROVIDER=bedrock` in production is
the only change needed to switch from Groq to AWS — no endpoint code changes.

### Environment Variables (`.env` in project root — never commit)
```
DATABASE_URL=<supabase postgresql connection string>
SECRET_KEY=<jwt signing secret>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=<integer>

# LLM provider — "groq" for local dev, "bedrock" for production
LLM_PROVIDER=groq
GROQ_API_KEY=<your groq api key>

# SMS provider — Queen SMS (Cameroon). In use now for the choronko track.
QUEEN_SMS_API_KEY=<your queen sms api key>
QUEEN_SMS_SENDER_ID=HASH          # max 11 chars, shown as the SMS sender name
QUEEN_SMS_BASE_URL=https://api.queensms.net/v1

# Future — add when AWS is set up
# AWS_REGION=us-east-1
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
```

### SMS Provider Abstraction (mirrors the LLM rule)
All SMS sends go through a single `NotificationService` / `sms_service`
interface in `app/services/sms_service.py`. The active provider is read from
settings. Queen SMS is the only implementation for the MVP; swapping to SNS or
Twilio later is one new subclass + one settings change — no caller code changes.
Queen SMS endpoint: `POST {QUEEN_SMS_BASE_URL}/sms.php` with form fields
`api_key`, `senderid`, `sms`, `mobiles` (comma-separated, "237" prefix optional).
Success = JSON `responsecode == 1`. Never call `requests`/`httpx` to Queen SMS
directly from a router or business-logic file — always via `sms_service`.

---

## 4. Completed Modules

### M1 — Identity & Onboarding ✅ (updated: personnel normalized + full CRUD)
Three tables now — `hospitals`, `personnel`, and `patients`. No shared users
table, no inheritance. Personnel are MANAGED RECORDS, not login users: the
hospital is the only hospital-side login. JWT `type` is only "hospital" or
"patient".

**Hospitals table:**
id, name, phone (unique, login ID), hashed_password, gps_lat, gps_lng,
address, is_active (bool, default True — soft delete), created_at
(NOTE: `personnel_name`/`personnel_contact` were REMOVED and normalized into the
personnel table.)

**Personnel table (one hospital → many personnel):**
id, hospital_id (FK → hospitals.id), name, phone (contact only, not a login),
email (nullable), role ("doctor"|"midwife"|"nurse"|"admin"), created_at,
updated_at. No password (no login). Hard-deletable.

**Patients table:**
id, name, phone (unique, login ID), hashed_password, is_active (bool, default
True — soft delete), created_at,
hospital_id (FK → hospitals.id),
weeks_pregnant_at_signup (int, 1–42),
lmp (computed: today − weeks×7 days),
edd (computed: lmp + 280 days),
account_type (default "smartphone"),
history_of_pregnancy_loss (bool, placeholder),
history_of_smoking (bool, placeholder),
known_chronic_conditions (str, placeholder)

**Endpoints (M1 + CRUD update):**
- POST /auth/hospital/signup  ← creates hospital + first personnel (one transaction)
- POST /auth/hospital/login
- POST /auth/patient/signup  ← computes and stores lmp + edd; is_active=True
- POST /auth/patient/login
- GET  /auth/me
- Hospital CRUD: GET /hospitals (public, active only), GET/PATCH/DELETE /hospitals/{id} (self; DELETE = soft)
- Personnel CRUD (hospital-only, own-hospital scoped): POST/GET /hospitals/{id}/personnel, PATCH/DELETE /personnel/{id} (DELETE = hard)
- Patient CRUD: GET /patients (hospital only, own patients), GET/PATCH/DELETE /patients/{id} (patient-self OR owning hospital; DELETE = soft)

**Access-control model (enforced via reusable dependencies):**
- Patient → only their own patient record; cannot list patients; no hospital/personnel access.
- Hospital → own hospital + own personnel + ONLY its own patients (scoped by hospital_id).
- Out-of-scope patient/personnel requests return 404 (not 403) to avoid leaking which ids exist elsewhere.
- Personnel are managed records, not logins.

**EDD utility:** `app/utils/pregnancy.py` → `compute_lmp_and_edd(weeks)`

### M2 — Patient & Pregnancy Profile ✅
Real weighted questionnaire, system-computed baseline risk level at signup
(config-driven rubric in `app/core/risk_config.py`), `pregnancies` and
`risk_assessments` tables, clinician risk-level override (logged), and
chat-message loss detection (keyword layer → Groq confirmation). See sections
6–9 for the full schema and endpoints.

### M3 — Conversation Engine + Message Triage ✅
Multi-turn chat pipeline (`app/routers/chat.py`), message store
(`app/models/message.py`, `app/services/message_store.py` with
`save_inbound` / `save_outbound`), per-message Low/Medium/High triage (M4 rules
folded in), and risk-based check-in cadence. The `messages` table and
`save_outbound` helper are the delivery substrate the appointment reminders
feature reuses — do not modify them.

---

## 5. Module Being Implemented Now

### Appointment Reminders (part of M3 / appointment surface)

An alarm-style reminder system for patient appointments. Four capabilities:

1. **Create** an appointment — patient submits a title, optional notes, and the
   appointment datetime. Hospital is taken from the patient's own record (the
   patient never submits `hospital_id`).
2. **List** appointments — a patient sees only their own; a hospital user sees
   all appointments for their hospital. Soft-deleted rows are never returned.
3. **Delete** one or many — soft delete (`is_deleted=True`), never hard delete.
   Each row is access-controlled individually.
4. **Remind** — a background scheduler fires a 24h reminder and a 2h reminder
   before each appointment, exactly once each, like an alarm.

**Delivery — two formats, chosen by `account_type`:**
- **Choronko patients → SMS** via Queen SMS (`sms_service`). SMS-safe short form.
- **Smartphone patients → in-app.** The reminder is written to the `messages`
  table (`direction="out"`, `message_type="reminder"`) so it appears in chat,
  **and** flagged unread so the app's notification bell/banner can poll it
  (this poll-flag is what stands in for a "push notification" in the MVP — true
  FCM push is Phase 2).

The reminder text is warm, plain, and uses the patient's name. Every reminder
has both a full in-app form and an SMS-safe short form (hard rule 15).

> Reminders reuse the existing `messages` table and `save_outbound` helper from
> M3. The scheduler runs in-process via APScheduler for local dev; in production
> it becomes EventBridge + Lambda with the same job functions.

---

### Reference — M2 design (already implemented)

M2 has four jobs:

**Job 1 — Replace dummy signup questions with Dr Elvira's real questionnaire**
The three placeholder columns in M1 become real weighted clinical questions.
Each question has a point value. The system sums the points to get a risk score.

**Job 2 — Compute baseline risk level automatically at signup**
System-computed, not clinician-assessed. Formula:
```
score = sum of weights from questionnaire answers
score 0–3  → "low"
score 4–7  → "medium"
score 8+   → "high"
```
Weights and thresholds live in a config dict (not hardcoded) so they can be
tuned without a code change. Result stored on patient record. Full audit trail
in risk_assessments table.

Clinicians can still override the computed level from the dashboard.
Every change (system or clinician) is logged in risk_assessments.

**Job 3 — Detect pregnancy loss from patient chat messages**
The patient reports a loss in the chat — the system detects it and acts.
A clinician does NOT trigger this. The detection pipeline is:

```
Layer 1 — Keyword matching (runs first, no API cost)
  → Scans message for known loss phrases
  → If no keyword match → treat as normal message

Layer 2 — LLM confirmation via Groq (runs only if Layer 1 fires)
  → Single classification call with a strict prompt
  → Returns: CONFIRMED | AMBIGUOUS | NOT_A_LOSS

If CONFIRMED:
  → Update patient status to "post_loss"
  → Record loss_date and ga_at_loss on the Pregnancy record
  → Stop all pregnancy reminders within 5 minutes (flag set in DB)
  → Set risk_level to "high" (physical recovery period)
  → Alert the hospital dashboard
  → Trigger M9 post-loss care track (send opening message)
  → Log to audit trail

If AMBIGUOUS:
  → Bot asks a gentle follow-up:
    "I want to make sure I understand — are you telling me you've
    experienced a pregnancy loss?"
  → Store pending_loss_confirmation = true on patient record
  → Wait for next message to re-evaluate

If NOT_A_LOSS:
  → Route through normal M4 triage pipeline
```

**Job 4 — Keep gestational age current**
`current_ga_weeks` is computed on the fly from stored `lmp`:
```python
current_ga_weeks = (date.today() - patient.lmp).days // 7
```
No scheduled job needed for MVP. Computed whenever needed.

---

## 6. New Database Entities for M2

### `pregnancies` table
Created automatically when a patient signs up. One pregnancy per patient for MVP.

```
id                UUID PK
patient_id        UUID FK → patients.id
lmp               DATE
edd               DATE
current_ga_weeks  INTEGER  (computed on read, not stored — or updated lazily)
outcome           VARCHAR  "ongoing" | "live_birth" | "loss"
loss_date         DATE     null unless outcome = "loss"
ga_at_loss        INTEGER  null unless outcome = "loss"
routine_paused    BOOLEAN  default False — True when loss detected, stops reminders
created_at        TIMESTAMP WITH TZ
```

### `risk_assessments` table
Audit trail for every risk level decision.

```
id              UUID PK
patient_id      UUID FK → patients.id
computed_at     TIMESTAMP WITH TZ
computed_by     VARCHAR  "system" | clinician UUID
inputs          JSONB    questionnaire answers or clinician reason
rubric_version  VARCHAR  e.g. "v1.0"
result_level    VARCHAR  "low" | "medium" | "high"
score           INTEGER  raw point total (for system computations)
```

### `appointments` table (appointment reminders feature)
One row per appointment a patient books. Soft delete only.

```
id                   UUID PK
patient_id           UUID FK → patients.id
hospital_id          UUID FK → hospitals.id   (taken from patient, not submitted)
title                VARCHAR  e.g. "Antenatal check-up", "Scan"
notes                TEXT     nullable
appointment_datetime TIMESTAMP WITH TZ  must be in the future at creation
reminder_24h_sent    BOOLEAN  default False
reminder_2h_sent     BOOLEAN  default False
is_deleted           BOOLEAN  default False — soft delete, keep for audit
created_at           TIMESTAMP WITH TZ
updated_at           TIMESTAMP WITH TZ
```

### `messages` table additions for notification polling
The appointment feature relies on a read flag the app polls for the bell/banner:
```
message_type  VARCHAR   "reply" | "reminder" | "checkin" | "crisis"
is_read       BOOLEAN   default False — only meaningful for direction="out"
                        reminder/checkin/crisis messages; True once acknowledged
```
If `message_type` already exists from M3, reuse it; only add `is_read` if missing.

### Updated `patients` table (M2 adds these columns)
```
-- Replace placeholder questions with real ones:
age                       INTEGER      (moved to proper validated column)
parity                    INTEGER      number of prior births
previous_loss             BOOLEAN      default False
previous_stillbirth       BOOLEAN      default False
previous_caesarean        BOOLEAN      default False
previous_preeclampsia     BOOLEAN      default False
has_hypertension          BOOLEAN      default False
has_diabetes              BOOLEAN      default False
has_sickle_cell           BOOLEAN      default False
has_hiv                   BOOLEAN      default False
has_severe_anaemia        BOOLEAN      default False
multiple_pregnancy        BOOLEAN      default False
late_anc_initiation       BOOLEAN      default False
no_prior_anc              BOOLEAN      default False

-- Risk output (set by system at signup, overrideable by clinician):
risk_level                VARCHAR      "low" | "medium" | "high"
risk_level_set_at         TIMESTAMP WITH TZ
risk_level_set_by         VARCHAR      "system" | clinician_id

-- Status:
status                    VARCHAR      "active" | "post_loss" | "delivered"
pending_loss_confirmation BOOLEAN      default False

-- Profile:
language                  VARCHAR
preferred_support         VARCHAR      "none" | "faith" | "peer" | "counsellor"
```

---

## 7. Risk Scoring Config (tunable without code change)

Lives in `app/core/risk_config.py`. Dr Elvira adjusts values here. Weights are
scaled to published effect sizes (odds ratios) for each factor's association
with pregnancy loss. This is a clinically-informed heuristic, NOT a validated
instrument — it decides who needs closer monitoring, never a probability quoted
to a patient. See `HASH_Risk_Rubric_for_Review.docx` for sources and rationale.

Some factors are graded (age, previous-loss count), so they use small helper
maps rather than a single flat weight. The scoring function must read every
value from this config — never hardcode.

```python
RUBRIC_VERSION = "v2.0"

# Graded factors (the answer picks the band, the band gives the points)
AGE_WEIGHTS = {
    "ge40_or_lt16": 4,   # age >= 40 OR < 16
    "35_to_39":     2,   # age 35-39
    "16_to_34":     0,   # age 16-34
}

PREVIOUS_LOSS_WEIGHTS = {   # number of prior pregnancy losses
    "ge3": 5,   # 3 or more
    "2":   3,
    "1":   2,
    "0":   0,
}

# Flat boolean factors (present → points)
QUESTION_WEIGHTS = {
    "has_sickle_cell":        4,
    "has_hypertension":       3,
    "has_diabetes":           3,
    "previous_stillbirth":    3,
    "previous_preeclampsia":  3,
    "multiple_pregnancy":     3,
    "has_hiv":                2,
    "has_severe_anaemia":     2,
    "previous_caesarean":     1,
    "first_trimester":        1,   # weeks_pregnant_at_signup < 13
    "parity_extreme":         1,   # parity == 0 OR parity >= 5
}

RISK_THRESHOLDS = {
    "high":   9,    # score >= 9 → high
    "medium": 4,    # score >= 4 → medium
                    # score < 4  → low
}

# Cadence in DAYS, keyed by risk level. High ~= 2x Medium frequency.
CHECK_IN_CADENCE_DAYS = {
    "high":   3,
    "medium": 7,
    "low":    14,
}

# Missed-response escalation: consecutive missed check-ins that flag a clinician
MISSED_CHECKIN_ESCALATION = {
    "high":   3,
    "medium": 2,
    # low: not escalated in MVP
}
```

NOTE — collected at signup but deliberately NOT scored (weight 0): gravidity
(double-counts with parity + losses), blood_group (weak loss predictor; Rh-
negative raises a clinical anti-D flag instead), distance_to_hospital (feeds
emergency logic, not baseline loss risk). The OLD placeholder fields
`late_anc_initiation` and `no_prior_anc` are dropped from scoring; remove them
from the rubric (keep the columns if already migrated, but they contribute 0).

The scoring function reads from this config — never from hardcoded values.

---

## 8. LLM Service Architecture

### `app/services/llm_service.py`

```python
# Interface:
class BaseLLMService(ABC):
    def classify_message(self, message: str, system_prompt: str) -> str: ...

# Implementations:
class GroqLLMService(BaseLLMService):   # LLM_PROVIDER=groq  → local dev
class BedrockLLMService(BaseLLMService): # LLM_PROVIDER=bedrock → production

# Factory (reads LLM_PROVIDER from settings):
def get_llm_service() -> BaseLLMService: ...

llm_service = get_llm_service()  # module-level singleton
```

All other modules import only `llm_service`. Never import Groq or boto3
directly in endpoint or business logic files.

### Loss Detection Prompts (`app/services/prompts.py`)

```python
LOSS_DETECTION_PROMPT = """
You are a medical assistant reviewing a patient message from a maternal
health platform. Determine if the patient is reporting a pregnancy loss
(miscarriage, stillbirth, or fetal death).

Reply with ONLY one of these three words:
CONFIRMED  — patient is clearly reporting a pregnancy loss
AMBIGUOUS  — message is unclear, could mean something else
NOT_A_LOSS — patient is not reporting a pregnancy loss

Do not add any explanation or punctuation. One word only.
"""

LOSS_KEYWORDS = [
    "lost my baby", "lost the baby", "lost my pregnancy",
    "had a miscarriage", "i miscarried", "miscarriage",
    "pregnancy loss", "lost my child", "stillbirth",
    "my baby died", "baby did not make it", "baby didn't make it",
    "i lost my pregnancy", "lost the pregnancy",
]
```

---

## 9. New M2 Endpoints

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `GET /patients/{id}` | GET | JWT (clinician or self) | Full profile with current GA, risk level, pregnancy |
| `GET /patients/{id}/pregnancy` | GET | JWT | Current pregnancy record |
| `PATCH /patients/{id}/risk-level` | PATCH | JWT (clinician only) | Override risk level — logged to risk_assessments |
| `GET /patients/{id}/risk-assessments` | GET | JWT (clinician) | Full risk level audit trail |
| `POST /chat/message` | POST | JWT (patient) | Receive patient message → run loss detection → triage |

The `POST /chat/message` endpoint is the entry point for all patient messages.
In M2 it handles loss detection. In M4 it will be extended with full triage.

### Appointment + Notification Endpoints (current feature)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `POST /appointments` | POST | JWT (patient) | Book an appointment; hospital taken from patient; datetime must be future |
| `GET /appointments` | GET | JWT (patient or hospital) | Patient sees own; hospital sees all of theirs. `?upcoming_only=true` optional |
| `DELETE /appointments/{id}` | DELETE | JWT (owner patient or hospital) | Soft-delete one appointment |
| `DELETE /appointments` | DELETE | JWT | Bulk soft-delete; returns `deleted` / `not_found` / `access_denied` lists |
| `GET /notifications/unread` | GET | JWT (patient) | Unread out reminder/checkin/crisis messages for the bell/banner |
| `POST /notifications/acknowledge` | POST | JWT (patient) | Mark listed messages read so the badge clears |

---

## 10. Folder Structure (updated for M2)

```
app/
├── main.py
├── core/
│   ├── config.py              # pydantic-settings Settings
│   ├── database.py            # engine, SessionLocal, Base, get_db
│   └── risk_config.py         # QUESTION_WEIGHTS, RISK_THRESHOLDS, RUBRIC_VERSION
├── models/
│   ├── hospital.py            # Hospital model ✅ done
│   ├── patient.py             # Patient model ✅ done
│   ├── pregnancy.py           # Pregnancy model ✅ done
│   ├── risk_assessment.py     # RiskAssessment model ✅ done
│   ├── message.py             # Message model ✅ done — ADD is_read column
│   └── appointment.py         # NEW: Appointment model
├── schemas/
│   ├── hospital.py            # ✅ done
│   ├── patient.py             # ✅ done
│   ├── pregnancy.py           # ✅ done
│   ├── risk_assessment.py     # ✅ done
│   ├── appointment.py         # NEW: AppointmentCreate/Response/DeleteRequest
│   └── common.py              # LoginRequest, TokenResponse ✅ done
├── routers/
│   ├── auth.py                # ✅ done
│   ├── hospitals.py           # ✅ done
│   ├── patients.py            # ✅ done
│   ├── chat.py                # ✅ done
│   ├── appointments.py        # NEW: create / list / delete (single + bulk)
│   └── notifications.py       # NEW: GET /unread, POST /acknowledge
├── services/
│   ├── llm_service.py         # ✅ done: Groq/Bedrock abstraction
│   ├── prompts.py             # ✅ done
│   ├── loss_detection.py      # ✅ done
│   ├── risk_scoring.py        # ✅ done
│   ├── message_store.py       # ✅ done: save_inbound / save_outbound
│   ├── sms_service.py         # NEW: Queen SMS abstraction (sms_service singleton)
│   ├── reminder_sender.py     # NEW: compose + dispatch 24h/2h reminders by channel
│   └── scheduler.py           # NEW: APScheduler job, every 15 min
└── utils/
    ├── auth.py                # ✅ done
    └── pregnancy.py           # ✅ done — compute_lmp_and_edd()
```

---

## 11. Hard Rules — Non-Negotiable

1. Bot never prescribes drugs or dosages.
2. All clinical content approved by Dr Elvira before release.
3. Patient data encrypted, access-logged, consent-based.
4. Pregnancy-loss content gentle, opt-out-friendly, never auto-triggered
   except by the loss detection pipeline which has LLM confirmation.
5. Phone number is the unique identifier. Never use email for login.
6. Never return `hashed_password` in any response.
7. Auth errors always generic — never confirm if a phone exists.
8. `hospital_id` on Patient is always a validated FK.
9. `lmp` and `edd` computed at signup, stored, never recomputed per request.
10. Risk level is system-computed at signup. Clinicians can override.
    Either way it is logged to risk_assessments.
11. Loss is detected from patient chat messages — not from clinician action.
    Always runs keyword check first, then LLM confirmation.
12. All LLM calls go through `llm_service` — never call Groq or boto3 directly
    in endpoint or business logic code.
13. `LLM_PROVIDER=groq` for local development. `LLM_PROVIDER=bedrock` for
    production. One env var change = full migration. No code changes.
14. Message acuity and patient risk level are independent. Never conflate them.
15. Choronko patients receive identical care — every message needs full + SMS form.
16. All secrets via `settings.*` — no hardcoded credentials.
17. All SMS sends go through `sms_service` (Queen SMS) — never call the Queen SMS
    HTTP endpoint directly from a router or business-logic file. Choronko →
    SMS; smartphone → in-app message + unread poll flag.
18. Appointments are soft-deleted only (`is_deleted=True`) — never hard delete.
    Each appointment is access-controlled per record on delete.
19. Reminders fire exactly once each (24h, 2h) — guard with
    `reminder_24h_sent` / `reminder_2h_sent`. One send failure must not stop the
    scheduler job (wrap each send in try/except).

---

## 12. AWS Free Tier — Accurate Information (verified May 2026)

**Your original assumption (1 year free) is outdated.**

For accounts created after July 15, 2025:
- $100 credits on signup + $100 more by completing 5 onboarding tasks = $200 total
- Credits expire after **6 months** (not 12)
- Bedrock has NO permanent free tier — it uses your credits, then pay-per-token
- Comprehend has a 12-month trial for older accounts; check current AWS page for new accounts

Always Free services (never expire, relevant to HASH):
- Lambda: 1M invocations/month
- SNS: 1M publishes/month
- SES: 62K emails/month
- S3: 5 GB storage
- DynamoDB: 25 GB (not used in HASH — using Supabase instead)

**Strategy:**
- Build everything locally with Groq (free, unlimited for dev)
- Set up AWS only when ready to deploy
- Use $200 credits for Bedrock testing in staging
- Set a $0 AWS Budget alert before your first AWS API call
- Every public IPv4 address costs $0.005/hour — release unused ones

---

## 13. What Is Out of Scope for MVP

- WhatsApp channel (Phase 2)
- Multi-tier referral graph (Phase 2)
- PHQ-9 and EPDS (Phase 2 — MVP ships PHQ-2 in M9 only)
- FHIR / EHR export (Phase 2)
- Insurance, billing, prescriptions (permanently excluded)
- Telehealth video

**M9 Post-Pregnancy-Loss Care IS in MVP scope.** It is a launch requirement.

---

*Last updated: Sprint 4 — Personnel normalization + full CRUD + access control*
*Status: M1 (updated), M2, M3 complete. Appointment reminders + risk scoring v2 built. Personnel table + CRUD + scoped access in progress.*
*Risk rubric pending Dr Elvira sign-off (see HASH_Risk_Rubric_for_Review.docx).*
*SMS provider: Twilio (outbound only — confirmed delivering to Cameroon). Inbound/two-way choronko SMS NOT supported by Twilio in Cameroon — separate provider (e.g. Africa's Talking) to be chosen later. Source: SRS HASH MVP v1.2 + product clarifications*