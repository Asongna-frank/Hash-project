# HASH Project — Full Context for AI Assistants

> Read this file before writing any code.
> Reference it in every Copilot Agent prompt.
> Update at the end of every sprint.

---

## 1. What HASH Is

HASH is a maternal care and early pregnancy loss support platform for
sub-Saharan African healthcare settings. An AI chatbot supports pregnant women
and women recovering from pregnancy loss. Hospitals monitor patients via a
clinical dashboard.

Two patient tracks — both first-class:
- **Smartphone** — mobile/web app: chat, tips, EDD countdown, emergency button
- **Choronko (GSM/SMS)** — feature phone only, full care over SMS

---

## 2. Two Core Concepts — Never Conflate

### Patient Risk Level
Computed automatically at signup from weighted questionnaire (Low/Medium/High).
Controls proactive check-in frequency. Clinician can override. Always logged.

### Message Acuity
Assigned per message in real time (Low/Medium/High).
Controls bot reply and whether to alert hospital.
Does NOT change patient risk level. Independent.

---

## 3. Tech Stack

| Layer | Tool | Notes |
|---|---|---|
| Backend | FastAPI | Running locally |
| Database | PostgreSQL via Supabase | Tables created via SQLAlchemy metadata, no Alembic |
| ORM | SQLAlchemy | |
| Auth | python-jose + passlib bcrypt | |
| LLM (dev) | Groq API — llama3-8b-8192 | |
| LLM (prod) | Amazon Bedrock | Future — swap via LLM_PROVIDER env var |
| Scheduler (dev) | APScheduler inside FastAPI | |
| Scheduler (prod) | EventBridge + Lambda | Same logic, different trigger |

### Environment Variables
```
DATABASE_URL=
SECRET_KEY=
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=
LLM_PROVIDER=groq
GROQ_API_KEY=
```

### LLM Abstraction Rule
All LLM calls go through `app/services/llm_service.py` only.
Never import Groq or boto3 directly in business logic files.
Switching to Bedrock = change one .env variable. Zero code changes.

---

## 4. Completed Modules

### M1 — Identity & Onboarding ✅

**Two completely separate tables — no inheritance, no shared users table.**

`hospitals` — id, name, phone (unique login ID), hashed_password,
gps_lat, gps_lng, address, created_at

`patients` — id, name, phone (unique login ID), hashed_password,
hospital_id (FK→hospitals), weeks_pregnant_at_signup, lmp, edd,
account_type, age, parity, language, preferred_support,
previous_loss, previous_stillbirth, previous_caesarean,
previous_preeclampsia, has_hypertension, has_diabetes,
has_sickle_cell, has_hiv, has_severe_anaemia, multiple_pregnancy,
late_anc_initiation, no_prior_anc,
risk_level, risk_level_set_at, risk_level_set_by,
status (active|post_loss|delivered),
pending_loss_confirmation, created_at

**Endpoints:**
- POST /auth/hospital/signup
- POST /auth/hospital/login  → returns JWT
- POST /auth/patient/signup  → computes lmp, edd, risk_level, creates Pregnancy record
- POST /auth/patient/login   → returns JWT
- GET  /auth/me
- GET  /hospitals            → public list for signup dropdown

### M2 — Patient & Pregnancy Profile ✅

**New tables created:**

`pregnancies` — id, patient_id (FK), lmp, edd, outcome (ongoing|live_birth|loss),
loss_date, ga_at_loss, routine_paused, created_at

`risk_assessments` — id, patient_id (FK), computed_at, computed_by,
inputs (JSONB), rubric_version, result_level, score

**Risk scoring:** weighted questionnaire → total score → Low/Medium/High.
Weights in `app/core/risk_config.py`. Every computation logged to risk_assessments.

**Loss detection pipeline (from patient chat):**
Layer 1: keyword match (free, instant)
Layer 2: Groq classification → CONFIRMED | AMBIGUOUS | NOT_A_LOSS
CONFIRMED → status="post_loss", pregnancy updated, risk→high, hospital alerted (TODO M6)

**Endpoints:**
- GET   /patients/{id}
- GET   /patients/{id}/pregnancy
- PATCH /patients/{id}/risk-level  (clinician only)
- GET   /patients/{id}/risk-assessments
- POST  /chat/message  (entry point — currently handles loss detection only)

**Services:**
- app/services/llm_service.py      — Groq/Bedrock abstraction
- app/services/prompts.py          — all prompt strings + keyword lists
- app/services/loss_detection.py   — two-layer pipeline
- app/services/risk_scoring.py     — compute_risk_level()
- app/core/risk_config.py          — weights, thresholds, rubric version

---

## 5. Module Being Implemented Now

### M3 — Conversation Engine (Phase 1: Chat + Triage)

**Scope for this sprint:**
- Conversation memory (short-term + patient context)
- Single Groq call returns reply + triage_level together
- Message logging (every inbound and outbound message stored)
- PAUSE / STOP / RESUME keyword handling
- Extend POST /chat/message with full pipeline

**Tips and reminders are out of scope for this sprint.**
**Personnel restructure is out of scope for this sprint.**

---

## 6. M3 Conversation Pipeline (full flow)

Every inbound patient message goes through these steps in order:

```
1. Save inbound message to messages table
2. Check PAUSE / STOP / RESUME → if match, handle and return immediately
3. Check patient.status == "post_loss" → route to post-loss handler (stub for now)
4. Check patient.pending_loss_confirmation → handle ambiguous loss follow-up
5. Run loss detection (keyword + Groq if needed)
6. If NOT_A_LOSS → run main conversation pipeline:
     a. Fetch last 10 messages from messages table (conversation memory)
     b. Build patient context block from patients + pregnancies tables
     c. Call Groq → returns {reply, triage_level} as JSON
     d. Save outbound reply to messages table
     e. Return {reply, triage_level} to patient
```

---

## 7. New Database Entity — `messages` Table

Every message in and out is stored here. This is the memory store.

```
id              UUID PK
patient_id      UUID FK → patients.id
direction       VARCHAR  "in" | "out"
channel         VARCHAR  "app" | "sms"
content         TEXT
message_type    VARCHAR  "chat" | "checkin" | "tip" | "reminder" | "crisis"
triage_level    VARCHAR  null for outbound | "low"|"medium"|"high" for inbound
created_at      TIMESTAMP WITH TZ
```

---

## 8. Conversation Memory — How Context is Built

Before every Groq call, the system assembles a context package:

**Patient context block (from DB — static per request):**
```
Patient: {name}
Gestational week: {current_ga_weeks}
Risk level: {risk_level}
Hospital: {hospital name}
Status: {active | post_loss}
Known conditions: {list of True flags from clinical profile}
```

**Conversation history (last 10 messages from messages table):**
```
Patient: {content}
Bot: {content}
Patient: {content}
... (up to 10 messages, oldest first)
```

These two blocks are injected into the Groq system prompt on every call.
current_ga_weeks is computed on the fly: `(date.today() - patient.lmp).days // 7`

---

## 9. Single Groq Call — Reply + Triage Together

One API call returns both the reply and the triage level.
The prompt instructs Groq to return strict JSON.

**System prompt structure:**
```
You are a warm, non-prescriptive maternal health assistant working in
sub-Saharan Africa. You support pregnant women through their pregnancy
and after pregnancy loss.

Rules you must always follow:
- Never prescribe medication or dosages
- Never speculate about causes of symptoms
- If the patient reports danger signs, tell her to go to hospital immediately
- Keep replies short, warm, and plain — no medical jargon
- Use the patient's name
- Never use exclamation marks
- Never use phrases like "everything happens for a reason"

Triage rules:
- high: heavy bleeding, severe pain, no fetal movement, severe headache,
        blurred vision, fever, suicidal ideation, self-harm language
- medium: mild pain, dizziness, nausea, worry, unusual but non-urgent symptoms
- low: general questions, reassurance, routine updates, normal pregnancy questions

Patient context:
{patient_context}

Conversation history:
{conversation_history}

Return ONLY valid JSON in this exact format, no explanation, no markdown:
{"reply": "your response here", "triage_level": "low|medium|high"}
```

**Response handling:**
```python
raw = llm_service.classify_message(message, system_prompt)
parsed = json.loads(raw)
reply = parsed["reply"]
triage_level = parsed["triage_level"]  # "low" | "medium" | "high"
```

If JSON parsing fails (model returns malformed output) → fallback:
```python
reply = "I received your message. Please contact your hospital if this is urgent."
triage_level = "medium"  # conservative default
```

If triage_level is "high" → TODO M6: alert hospital in real time.

---

## 10. PAUSE / STOP / RESUME Handling

Checked before any other logic. Case-insensitive. Exact word match.

| Keyword | Effect | Bot reply |
|---|---|---|
| PAUSE | Set opt_out_status="paused", paused_until=now+7days | Single warm confirmation, no follow-up |
| STOP  | Set opt_out_status="stopped" | Single warm confirmation, no follow-up |
| RESUME | Clear opt_out_status, clear paused_until | Single warm confirmation |

Add to patients table:
```
opt_out_status   VARCHAR   null | "paused" | "stopped"
paused_until     TIMESTAMP null | datetime (set when PAUSE received)
```

Scheduler must check opt_out_status before sending any proactive message.

---

## 11. Updated Chat Response Schema

```json
{
  "reply": "string",
  "triage_level": "low | medium | high",
  "loss_detected": false
}
```

`loss_detected` carried forward from M2 for the frontend to handle
post-loss UI changes.

---

## 12. Folder Structure (current state)

```
app/
├── main.py
├── core/
│   ├── config.py
│   ├── database.py
│   └── risk_config.py
├── models/
│   ├── hospital.py
│   ├── patient.py
│   ├── pregnancy.py
│   ├── risk_assessment.py
│   └── message.py              ← NEW in M3
├── schemas/
│   ├── hospital.py
│   ├── patient.py
│   ├── pregnancy.py
│   ├── risk_assessment.py
│   ├── common.py
│   └── message.py              ← NEW in M3
├── routers/
│   ├── auth.py
│   ├── hospitals.py
│   ├── patients.py
│   └── chat.py                 ← UPDATED in M3
├── services/
│   ├── llm_service.py
│   ├── prompts.py              ← UPDATED in M3
│   ├── loss_detection.py
│   ├── risk_scoring.py
│   └── conversation.py         ← NEW in M3
└── utils/
    ├── auth.py
    └── pregnancy.py
```

---

## 13. Hard Rules

1. Bot never prescribes drugs or dosages
2. All clinical content approved by Dr Elvira
3. Phone number is the unique identifier — never email
4. Never return hashed_password in any response
5. Auth errors always generic
6. hospital_id on Patient always a validated FK
7. lmp and edd computed at signup, stored, never recomputed per request
8. Risk level system-computed at signup, clinician can override, always logged
9. Loss detected from patient chat — never inferred silently, always LLM-confirmed
10. All LLM calls through llm_service only
11. LLM_PROVIDER=groq for dev, bedrock for prod — one env var, no code changes
12. Message acuity and patient risk level are independent — never conflate
13. PAUSE/STOP/RESUME checked before any other message logic
14. JSON parse failures in LLM responses always have a safe fallback
15. Every inbound and outbound message saved to messages table

---

## 14. Pending for Future Sprints

- Personnel table (hospital has many named staff profiles, one hospital login)
- Tips system (RAG-based, scheduled daily generation)
- Appointment reminders
- M4 advanced triage refinement
- M6 real-time hospital alerting
- M9 post-loss care track
- Choronko SMS channel
- AWS migration (EventBridge + Lambda replaces APScheduler)

*Last updated: Sprint 3 — M3 Conversation + Triage*
*M1 ✅  M2 ✅  M3 (conversation + triage) in progress*