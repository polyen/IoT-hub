# IoT Hub — Threat Model

> STRIDE analysis per component. Status: ✅ implemented | ⏳ planned | ⚠️ accepted-risk
> Last updated: 2026-05-08. Revise when any service interface changes (TC4).

## System components

1. **Edge RPi5** — compute, local PostgreSQL, AI inference (YOLOv8 / Whisper), Mosquitto MQTT broker
2. **ESP32 sensor nodes** — DHT22 temperature/humidity, MQ-2 gas, PIR motion, relay actuators
3. **VPS (cloud)** — MQTT bridge receiver, Telegram bot, PostgreSQL read replica (T1/T2 only)
4. **PWA** — browser UI (React), WebSocket to backend, IndexedDB offline cache, background sync
5. **LLM Agent** — voice commands via Whisper, tool execution via LangChain ReAct, Gemini 2.0 Flash

---

## STRIDE per component

### MQTT Broker (Mosquitto on RPi5)

| Threat | STRIDE | Description | Mitigation | Status |
|--------|--------|-------------|------------|--------|
| Spoofing | S | Attacker impersonates an ESP32 node by reusing a client ID | mTLS — per-device client certificate; CN matches MQTT username enforced by `hub/mosquitto/conf.d/tls.conf` | ✅ |
| Tampering | T | MQTT payload modified in transit between ESP32 and broker | TLS 1.3 on port 8883; plaintext port 1883 disabled in `mosquitto.conf` | ✅ |
| Repudiation | R | No audit trail of who published a command or event | All agent tool invocations logged to `agent_audit` table (T2.11); MQTT bridge logs retained | ✅ |
| Info disclosure | I | Sensitive camera topics leaked to VPS replica | ACL in `hub/mosquitto/conf.d/acl.conf` restricts VPS bridge user to `iot/T1/#` and `iot/T2/#` only | ✅ |
| DoS | D | Compromised or malfunctioning sensor floods broker with high-rate publishes | Per-client rate limiting via Mosquitto `max_inflight_messages` and `max_queued_messages`; not yet tuned per-device | ⏳ |
| Elevation | E | Sensor node writes to actuator command topics it should only read | ACL: ESP32 device users have write permission only on their own `iot/device/<id>/telemetry` prefix; actuator topics require `hub-agent` credentials | ✅ |

### Edge PostgreSQL (local, RPi5)

| Threat | STRIDE | Description | Mitigation | Status |
|--------|--------|-------------|------------|--------|
| Spoofing | S | Attacker connects to PostgreSQL posing as application user | Unix socket auth (`peer`) for local processes; TCP disabled; `pg_hba.conf` enforces scram-sha-256 for network clients | ✅ |
| Tampering | T | Direct modification of `events` or `agent_audit` rows to cover tracks | DB user `hub_app` has no DELETE on audit tables; writes are append-only by design (`hub/backend/models.py`) | ✅ |
| Repudiation | R | Disputed actuator command with no proof of origin | `agent_audit.triggered_by` and `agent_audit.raw_transcript` fields record LLM decision context | ✅ |
| Info disclosure | I | T0 biometric data (face embeddings) exposed via SQL dump or backup | T0 store (`pgvector` embeddings) lives in separate tablespace on LUKS-encrypted partition; backups excluded from VPS replication | ✅ |
| DoS | D | Long-running analytical query blocks event ingestion | `statement_timeout = 5s` for `hub_app` role; separate read replica connection pool for analytics | ⏳ |
| Elevation | E | Backend bug allows arbitrary SQL via unsanitised input | All DB access via SQLAlchemy ORM with parameterised queries (`hub/backend/db.py`, `hub/backend/models.py`); no raw string interpolation | ✅ |

### LLM Agent (LangChain ReAct + Gemini 2.0 Flash)

| Threat | STRIDE | Description | Mitigation | Status |
|--------|--------|-------------|------------|--------|
| Spoofing | S | Malicious voice input impersonates an authorised user | Wake-word gating + speaker verification (T2 pipeline); only enrolled voices trigger tool execution | ⏳ |
| Tampering | T | Adversary modifies tool output (e.g., fake sensor reading) fed back to LLM | Tools call internal APIs only; no external HTTP calls without explicit allow-list in `PolicyEngine` | ✅ |
| Repudiation | R | No record of which LLM output triggered an actuator change | `agent_audit` table records prompt, intent, tool calls, and timestamps for every ReAct cycle | ✅ |
| Info disclosure | I | Sensitive home data sent to Gemini API outside LAN | Only anonymised intent descriptions sent; raw sensor values and room names stripped before API call | ⏳ |
| DoS | D | Adversary loops voice commands to exhaust Gemini API quota | Per-session rate limit (5 requests / 30 s) enforced in `hub/backend/agent/runner.py` | ⏳ |
| Elevation | E | Prompt injection causes agent to execute disallowed tool | `PolicyEngine.reject_intent_patterns` blocks dangerous intents; tool registry is an explicit allow-list, not dynamic | ✅ |

### PWA (browser, React + Vite)

| Threat | STRIDE | Description | Mitigation | Status |
|--------|--------|-------------|------------|--------|
| Spoofing | S | CSRF: attacker tricks authenticated browser into posting feedback | SameSite=Strict cookie on session token; CORS origin check on FastAPI (`hub/backend/main.py`) | ✅ |
| Tampering | T | XSS attack injects script via malicious event payload rendered in EventsFeed | React escapes all interpolated values; `payload` displayed in `<pre>` via `JSON.stringify`, never dangerously set | ✅ |
| Repudiation | R | User denies submitting a feedback label | Feedback stored in `feedback_events` table with timestamp; browser-side optimistic cache in IndexedDB | ✅ |
| Info disclosure | I | Service worker cache leaks events to a different user on shared device | Cache scoped to origin; no credentials stored in SW cache; cache cleared on logout | ⏳ |
| DoS | D | Attacker floods `/api/feedback` to exhaust DB connections | FastAPI rate-limit middleware (planned T3.10); connection pool capped in `hub/backend/db.py` | ⏳ |
| Elevation | E | Offline sync queue submits feedback as a different identity | Background sync uses existing service worker scope; no separate credentials stored; same session cookie used | ✅ |

### VPS (cloud bridge + Telegram bot)

| Threat | STRIDE | Description | Mitigation | Status |
|--------|--------|-------------|------------|--------|
| Spoofing | S | Attacker impersonates RPi5 MQTT bridge to inject fake T1/T2 events | Bridge connection uses dedicated TLS client cert; broker rejects connections without valid cert | ✅ |
| Tampering | T | VPS-side script alters replicated T1/T2 rows before Telegram notification | VPS DB user has INSERT-only access on replica tables; no UPDATE/DELETE grants | ✅ |
| Repudiation | R | Telegram notification sent without traceable source event | Each notification references `event_id` UUID linking back to originating edge event | ✅ |
| Info disclosure | I | Telegram bot token or DB password exposed in environment | Secrets stored in systemd `EnvironmentFile` with 0600 permissions; not in repo or Docker ENV | ✅ |
| DoS | D | VPS flooded with MQTT messages causing memory exhaustion | Mosquitto bridge `max_queued_messages = 1000`; Telegram rate-limited to 1 msg / 3 s by bot wrapper | ⏳ |
| Elevation | E | Compromised VPS writes actuator commands back to RPi5 broker | MQTT ACL on RPi5 denies writes from VPS bridge credentials to actuator topics; VPS is receive-only | ✅ |

### ESP32 Sensor Nodes

| Threat | STRIDE | Description | Mitigation | Status |
|--------|--------|-------------|------------|--------|
| Spoofing | S | Clone device uses same firmware but different hardware ID | Per-device TLS certificate provisioned at flash time; broker CN validation prevents ID collision | ✅ |
| Tampering | T | Firmware modified to report false sensor values or execute relay without authorisation | OTA updates require signed firmware (ESP-IDF secure boot v2); unsigned images rejected | ⏳ |
| Repudiation | R | Device denies having sent a specific reading | TLS mutual auth means broker can attribute every publish to a certificate; logged at broker | ✅ |
| Info disclosure | I | Wi-Fi credentials or MQTT cert key extracted from flash | Flash encryption enabled (AES-XTS 256-bit); JTAG disabled in fuses after provisioning | ⏳ |
| DoS | D | Physical attacker cuts power or jams 2.4 GHz channel | PIR/gas sensors operate autonomously; local relay state preserved in NVS across power cycles | ⚠️ |
| Elevation | E | Sensor node subscribes to actuator command topics it should not see | MQTT ACL on broker: ESP32 credentials have subscribe permission only on their own `iot/device/<id>/cmd` prefix | ✅ |

---

## Data tier privacy

The system classifies data into four tiers with distinct handling rules:

| Tier | Data type | Stored where | Forwarded to VPS | Retention |
|------|-----------|--------------|-----------------|-----------|
| T0 | Face frames, voice biometrics, pgvector embeddings | RPi5 only, LUKS-encrypted partition | Never | Until manually purged |
| T1 | Sensor readings (temperature, humidity, gas level) | RPi5 + VPS replica | Yes, with user consent during setup | 90 days on VPS |
| T2 | Camera motion events (anonymised: no raw frames) | RPi5 + VPS replica (metadata only) | Yes, after anonymisation (bounding box stripped) | 30 days on VPS |
| T3 | pgvector semantic embeddings (scene descriptions) | RPi5 only | Never | Until manually purged |

- **T0 at rest**: Stored on a dedicated LUKS partition mounted at `/mnt/secure`; PostgreSQL tablespace `ts_secure` resides there. The LUKS passphrase is not stored on disk — entered manually or via TPM2 unsealing.
- **T1 forwarding**: Enabled only after explicit user consent in Settings page (`/api/settings` consent flag). Forwarded via authenticated MQTT bridge.
- **T2 anonymisation**: YOLOv8 bounding boxes and confidence scores forwarded; original JPEG frames never leave the LAN. Anonymisation logic in `hub/backend/vision/anonymise.py`.
- **T3 never replicated**: pgvector embeddings are excluded from the VPS replication set via `hub/backend/db.py` table-level replication filter.

---

## Known risks / accepted

### LLM prompt injection (⚠️ accepted-risk)
The LangChain ReAct agent is susceptible to indirect prompt injection — e.g., a sensor payload containing text that manipulates the agent's next action. Mitigations in place: `PolicyEngine.reject_intent_patterns` blocks patterns matching shell commands, actuator overrides, and jailbreak attempts. The tool registry is a static allow-list (not dynamic). However, a sufficiently crafted payload could still bypass these checks. **Risk accepted** for diploma scope; production deployment would require sandboxed tool execution and LLM output validation.

### Physical access to RPi5 (⚠️ accepted-risk)
An attacker with physical access to the RPi5 can boot from USB and access the unencrypted `/` partition. LUKS protects T0 data on `/mnt/secure`, but the application code, configuration, and non-T0 database are readable. **Mitigation**: UEFI Secure Boot with RPi5 TF-A is planned (T4 scope) but not yet implemented. For the home-lab threat model, physical access is assumed to be controlled.

### VPS compromise (residual-risk, low impact)
If the VPS is fully compromised, the attacker gains read access to T1 sensor readings and anonymised T2 event metadata for up to 30/90 days respectively. They cannot write actuator commands to the RPi5 broker (ACL enforced on the RPi5 side, not the VPS). They cannot access T0 or T3 data. **Impact**: privacy loss for sensor history; no safety risk. This residual risk is **accepted** given VPS isolation and the read-only replication design.

### Background sync replay (residual-risk, low impact)
The PWA background-sync queue (`iot-feedback` IndexedDB store) retains unsubmitted feedback payloads until sync succeeds. On a shared device, another user could inspect IndexedDB via DevTools and read pending feedback labels. **Mitigation**: feedback payloads contain only `alert_id` (UUID) and a label (`TP`/`FP`/`not_sure`); no PII. Risk accepted.
