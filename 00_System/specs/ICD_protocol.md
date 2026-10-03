# Bird Bend Stand — Interface Control Document: PC ⇄ FW protocol

| Doc | ICD_protocol |
|---|---|
| Version | **0.1 — DRAFT for the P1 gate** |
| Date | 2026-10-03 |
| Owner | Implementer C — Integrator (changes only with a version bump + change-history entry, IF-001) |
| Implements | SRS v0.2: IF-001…IF-012, FW-CFG-001…004, FW-NVM-001…003, FW-CMD-001…004, FW-STR-001…006, FW-TIM-001, FW-PAR-001…006 (Table 5.1), command semantics of SAF-FW-001…023 and SRS §3.2; decisions D-03, D-05, D-12…D-28; SRS deltas from R5 §8 / D-28 and SW_design F-B-01…06/15/19 (§14) |
| Protocol | **PROTO_VERSION 1.0**, **PAYLOAD_VERSION 1**, dictionary `params.yaml` dict_version 1 (hash in Appendix A) |
| Machine-readable companions | `00_System/tools/ref_codec.py` (codec oracle), `ref_cmdcheck.py` (acceptance oracle), `vectors/protocol_vectors.json`, `vectors/check_vectors.json` (§12) |
| Origin | framing, CRC, parser, PARAM_ENTRY, NVM and versioning rules follow Thrust_Stand_HAW `00_System/specs/ICD_protocol.md` @9473c68 (trimmed per R3 §1.6) |

---

## 0. Conventions

### 0.1 Language and encoding
- **MUST / MUST NOT / SHOULD / MAY** are normative. "FW" = NUCLEO-F446RE firmware (also its host twin),
  "SW" = PC application (also its simulator).
- All multi-byte fields are **little-endian** (IF-002). Offsets are in bytes from the start of the payload.
  Types: `u8/i8/u16/i16/u32/i32` two's complement, `f32` IEEE-754 binary32, `char[n]` ASCII NUL-padded.
- Wire units (IF-009, SYS-003): position **µm** (`i32`, machine coordinate), speed **µm/s**, acceleration
  **µm/s²**, time **µs** (device clock) or ms, load **raw HX711 counts** (`i32`, sign-extended 24-bit).
  No force or mm value is ever on the wire. All float→int conversions round half away from zero.
- Machine coordinate: x = 0 at the home reference, **+x points from the START switch toward the END
  switch** (`motion.dir_invert` is set at the hardware gate so that this holds). LIMIT_START is the −x end,
  LIMIT_END the +x end.
- When the protocol vectors and this text disagree, the **vectors are the oracle** for byte values and the
  text is corrected (R3 pitfall P14); a disagreement is reported to the Integrator.

### 0.2 Versioning (IF-008)
- `PROTO_VERSION major.minor`: **minor** bump = backward-compatible addition only (new command, new EVENT /
  STATUS / reason code, new bit in a reserved bit position, bytes **appended** to a response, INFO or STATUS
  body). **Major** bump = anything else.
- Receivers MUST ignore trailing bytes of OK responses longer than they know (vector
  `get_info_resp_longer`). The FW MUST reject commands whose LEN differs from §3.2 (`E_LENGTH`).
- `PAYLOAD_VERSION` (DATA byte 4) changes whenever the DATA payload layout changes; a receiver MUST NOT
  decode a DATA payload of an unknown version.
- `PARAM_DICT_HASH` (CRC-32 of the canonical dictionary, §11.1) is reported in GET_INFO and stored in NVM
  records.
- Unknown values received from the other side (EVENT code, status code, reason code, set reserved bits)
  MUST be tolerated (logged, not fatal).
- **Until ICD 1.0** (P1 baseline accepted by the PO) layouts may still change; every change bumps the ICD
  version, regenerates the vectors and is logged in §15. From ICD 1.0 on, the PROTO rules above are strict.

---

## 1. Physical layer (IF-002, D-03)
- NUCLEO-F446RE **USART2** (PA2 TX / PA3 RX) → ST-LINK/V2-1 virtual COM port (USB CDC) → PC.
- **921 600 Bd, 8 data bits, no parity, 1 stop bit, no flow control.** Link capacity 92 160 B/s.
  (USART2 on APB1 45 MHz: BRR 3 + 1/16 → 918 367 Bd, −0.35 %, within tolerance; R4 §9.) 921 600 Bd over the
  VCP is verified only at the hardware gate (SYS-009, OI-11).
- Binary, **no escaping / byte stuffing**; framing is recovered by sync + LEN + CRC (§2.3).
- The FW host twin serves the same byte stream on TCP `127.0.0.1:5760` (§12, tools/README).

## 2. Frame format (both directions) (IF-003, IF-004, PO-FW-10)

| Offset | Size | Field | Description |
|---|---|---|---|
| 0 | 1 | `SYNC0` | `0xA5` — separator (D-05) |
| 1 | 1 | `SYNC1` | `0x5A` |
| 2 | 1 | `TYPE` | message type (§3) |
| 3 | 1 | `SEQ` | link sequence number (§2.2) |
| 4 | 2 | `LEN` | payload length N, `u16`, **0 ≤ N ≤ 160** (`MAX_LEN`) |
| 6 | N | `PAYLOAD` | |
| 6+N | 2 | `CRC` | CRC-16 over bytes `[2 .. 6+N−1]` (TYPE, SEQ, LEN, PAYLOAD), low byte first |

Frame overhead = 8 bytes; largest frame = 168 bytes = 1.82 ms on the wire, so a DATA frame never waits more
than 2 ms behind another frame (FW-STR-002, FW_design OI-FW-02). Largest frame defined in this version:
GET_ALL_PARAMS response page, 152 bytes. STOP (9 B) and HALT (8 B) are short fixed-length frames with fixed
TYPEs so that the FW 1 kHz "stop sniffer" can detect them in the RX ring ahead of normal command processing
(FW_design OI-FW-11).

### 2.1 CRC (IF-004)
CRC-16/CCITT-FALSE: poly `0x1021`, init `0xFFFF`, no input/output reflection, xorout `0x0000`.
**Check value: ASCII `"123456789"` → `0x29B1`**; empty input → `0xFFFF`. Example: PING request SEQ 7 =
`A5 5A 01 07 00 00 E4 77` (CRC 0x77E4). Table-driven implementations MUST reproduce `crc16` vectors.

```c
uint16_t crc16_ccitt(const uint8_t *d, size_t n, uint16_t crc /* = 0xFFFF */) {
    while (n--) {
        crc ^= (uint16_t)(*d++) << 8;
        for (int i = 0; i < 8; i++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}
```
Frames with a bad CRC are dropped, counted (`rx_crc_errors` in the FW, a link counter in the SW) and never
acted upon (IF-004).

### 2.2 Sequence numbers (FW-CMD-001, FW-STR-004)
- **PC → FW**: the SW chooses `SEQ` per command (incrementing, wraps 255 → 0, skipping values still pending;
  the first SEQ after opening the port is randomised). The response carries the **same** SEQ.
- **DATA**: header `SEQ` = low byte of the payload `frame_seq` (§7.3). `frame_seq` (u16) starts at 0 at boot
  and is incremented once per DATA frame **due** while the stream is on — also when the frame had to be
  dropped in the FW (TX congestion) — and is not reset by STREAM_STOP/START. A `frame_seq` gap = lost
  frame(s); `OVERRUN` in the next sent frame says the loss happened in the FW.
- **EVENT**: header `SEQ` = event counter (u8, +1 per event incl. events dropped by queue overflow), so an
  EVENT SEQ gap = lost event(s) (`event_overflows` in STATUS).

### 2.3 Receiver state machine (both sides, normative) (IF-003)
The receiver keeps a byte buffer of **≥ 336 bytes** (≥ 2 maximum frames) so that bytes consumed by a failed
candidate can be re-scanned (or an equivalent implementation whose output — frames and counters — is
identical to `ref_codec.FrameParser` on all `streams` vectors).
1. **Hunt**: discard bytes until `A5 5A` is at the buffer head (a trailing lone `A5` is kept).
2. Wait for the 6 header bytes. If `LEN > 160` → `len_errors += 1`, discard **one** byte (the SYNC0), go to 1.
3. Wait for `LEN + 2` more bytes; compute the CRC over TYPE..PAYLOAD. Match → deliver the frame and remove the
   whole frame (`8 + LEN` bytes). Mismatch → `crc_errors += 1`, discard **one** byte (the SYNC0), go to 1 — a
   truncated frame followed by a real frame must not swallow the real one.
4. **Inter-byte timeout 20 ms**: if a candidate is incomplete and no byte arrived for 20 ms →
   `timeout_drops += 1`, discard one byte (its SYNC0) and re-scan the buffered bytes from step 1; further
   incomplete candidates in the timed-out buffer are dropped the same way; a lone trailing `A5` is discarded
   without counting. (SW: the timeout is evaluated only after an empty read.)
5. A CRC-valid frame with a TYPE not valid for the receiving side (§3.1) is counted (`rx_frame_errors` in the
   FW) and ignored without response.

### 2.4 Transmitter rules
- Frames are never interleaved: a frame is written completely before the next one starts (FW: one TX queue
  for responses, DATA and EVENT frames).
- FW: before each flash erase/program the TX queue is drained so no frame is split by the stall (FW-NVM-003).
- FW TX priority when the TX queue is congested: responses > EVENT > DATA; a DATA frame that cannot be queued
  within its slot is dropped (counted in `tx_drops`, `frame_seq` still advances, next frame has `OVERRUN`).
- SW: STOP, HALT, PAUSE and the clear commands are written through a priority path that bypasses every SW
  queue, lane, rate limit and outstanding-command limit; the longest wait is one frame already being written
  (IF-011, NFR-002/003).

---

## 3. Message types (IF-012)

### 3.1 TYPE ranges

| TYPE | Direction | Meaning |
|---|---|---|
| `0x01..0x3F` | PC → FW | commands (defined in §3.2; others → `E_UNKNOWN_CMD`) |
| `0x81..0xBF` | FW → PC | response to command `TYPE & 0x7F` |
| `0xC0` / `0xC1` | FW → PC | asynchronous DATA / EVENT (§7.3, §7.4) |
| `0xC2..0xCF` | FW → PC | reserved for future asynchronous frames (receivers ignore) |
| `0x00`, `0x40..0x80`, `0xD0..0xFF` | — | invalid (receiver: count, ignore, no response) |

A "valid command frame" (link watchdog, §9.1) is a CRC-valid frame with TYPE `0x01..0x3F`, including frames
that get a NACK.

### 3.2 Command list
LEN is fixed per command (`E_LENGTH` otherwise). "Retry" = SW retry class (§9.3). Response body = bytes after
the STATUS byte on OK (§4.1).

| TYPE | Name | Request payload (LEN) | Response body on OK | Retry | SRS |
|---|---|---|---|---|---|
| `0x01` | PING | – (0) | – | RETRY | SAF-SW-003, SAF-FW-015 |
| `0x02` | GET_INFO | – (0) | INFO §7.1 (44) | RETRY | FW-CFG-004, IF-008 |
| `0x03` | GET_STATUS | – (0) | STATUS §7.2 (84) | RETRY | FW-CMD-004 |
| `0x04` | REBOOT | `u32 magic = 0xB007B007` (4) | – | VERIFY | SAF-FW-018 |
| `0x10` | GET_ALL_PARAMS | `u8 page` (1) | `u8 page, u8 page_count, u8 n, n × PARAM_ENTRY` | RETRY | FW-CFG-002, PO-FW-5 |
| `0x11` | GET_PARAM | `u16 id` (2) | PARAM_ENTRY (7) | RETRY | FW-CFG-002 |
| `0x12` | SET_PARAM | PARAM_ENTRY (7) | PARAM_ENTRY as stored (7) | RETRY | FW-CFG-003, PO-FW-6 |
| `0x13` | SAVE_PARAMS | – (0) | – | VERIFY | FW-NVM-001 |
| `0x14` | LOAD_PARAMS | – (0) | – | VERIFY | FW-NVM-001 |
| `0x15` | DEFAULT_PARAMS | – (0) | – | VERIFY | FW-NVM-001 |
| `0x20` | STREAM_START | – (0) | – | RETRY | FW-STR-001, PO-FW-8 |
| `0x21` | STREAM_STOP | – (0) | – | RETRY | FW-STR-001 |
| `0x22` | SET_VALID | `u8 valid` 0/1 (1) | `u32 t_us` from which it applies | RETRY (newest value) | FW-CMD-002, PO-FW-7 |
| `0x30` | ENABLE | – (0) | `u16 settle_ms` (ms until ENABLED) | VERIFY | FW-MOT-008 |
| `0x31` | DISABLE | – (0) | – | VERIFY | FW-MOT-008 |
| `0x32` | HOME | `u8 flags` (1): bit0 = operator confirmed load, bits 1–7 = 0 | – | VERIFY | FW-HOM-001, SAF-FW-021 |
| `0x33` | MOVE_ABS | `i32 target_um, u32 v_um_s, u32 a_um_s2` (12) | – | VERIFY | FW-MOT-004 |
| `0x34` | JOG | `i32 v_um_s, u32 a_um_s2, i32 bound_um` (12) | – | JOG≠0: VERIFY (refresh stream); JOG 0: RETRY | FW-MOT-005, SAF-FW-016 |
| `0x35` | MOVE_UNTIL_LOAD | `i32 bound_um, u32 v_um_s, u32 a_um_s2, i32 raw_stop, u8 cmp` (17) | – | VERIFY | FW-MOT-006 |
| `0x36` | STOP | `u8 mode` (1): 0 = immediate, 1 = controlled | – | CONFIRM | FW-MOT-007, SW-STOP-001 |
| `0x37` | HALT | – (0) | – | CONFIRM | FW-MOT-007, SW-STOP-002 |
| `0x38` | HALT_CLEAR | – (0) | – | ONCE_PRIORITY | FW-MOT-007, SAF-FW-022 |
| `0x39` | ESTOP_CLEAR | – (0) | – | ONCE_PRIORITY | SAF-FW-006 |
| `0x3A` | FAULT_CLEAR | – (0) | `u16 cleared` (FAULT mask §7.6) | ONCE_PRIORITY | FW-CMD-003, SAF-FW-011 |
| `0x3B` | PAUSE | – (0) | – | CONFIRM | SAF-FW-023, D-14, D-26 (SRS delta, §14) |

There is **no relative-move command** (SAF-FW-020, IF-009): the SW converts ±0.1/1/10 mm and distance
entries into absolute targets.

Asynchronous (FW → PC): `0xC0` DATA (§7.3, IF-006), `0xC1` EVENT (§7.4, FW-STR-006).

---

## 4. Responses (FW-CMD-001, IF-005)

### 4.1 ACK / NACK
- Every CRC-valid command frame (TYPE `0x01..0x3F`) gets **exactly one** response: `TYPE | 0x80`, same `SEQ`.
- Payload byte 0 = `STATUS` (§4.2).
  - `STATUS = OK (0)`: the command-specific body of §3.2 follows.
  - `STATUS ≠ 0` (NACK): payload is **exactly 3 bytes**: `u8 status, u16 detail`.
    **A NACKed command has no effect** (no state change, no latch, no flash write); it still refreshes the
    link watchdog.
- Commands are executed in arrival order; DATA/EVENT frames may be sent between a command and its response.

### 4.2 STATUS codes and `detail`

| Code | Name | Meaning | `detail` |
|---|---|---|---|
| 0 | `OK` | accepted / executed | (none) |
| 1 | `E_UNKNOWN_CMD` | TYPE in `0x01..0x3F` not defined | the TYPE |
| 2 | `E_LENGTH` | LEN ≠ the command's fixed LEN | the expected LEN |
| 3 | `E_PARAM_ID` | unknown parameter id | the requested id |
| 4 | `E_TYPE` | PARAM_ENTRY type byte ≠ the parameter's type | the parameter id |
| 5 | `E_RANGE` | argument out of range (never clamped) | SET_PARAM: parameter id; other commands: **byte offset** of the offending field in the request payload |
| 6 | `E_CONFIG` | SET_PARAM violates a hard rule (§11.4) | id of the **other** parameter of the rule |
| 7 | `E_BUSY` | not possible now, retry later | 1 = MOTION (moving, homing or stopping), 2 = ENABLING (ENA settle running) |
| 8 | `E_STATE` | motion / enable refused in the current state | **BLOCK mask** (§4.3): all blocking conditions |
| 9 | `E_CAUSE_ACTIVE` | clear refused, cause still present | ESTOP_CLEAR: `0xFFFF` = sense input open, else ms still missing until `io.estop_release_ms`; HALT_CLEAR: `0xFFFF` = STOP button active, else ms still missing until `io.release_ms`; FAULT_CLEAR: FAULT mask (§7.6) of the latched faults whose cause is still present |
| 10 | `E_CONFIRM` | HOME with load above `home.max_load_raw` without the confirmed flag | 0 |
| 11 | `E_NVM` | NVM failure | 1 = no valid record (LOAD), 2 = erase/program error, 3 = verify error |
| 12 | `E_INTERNAL` | implementation error | implementation-defined |

### 4.3 BLOCK mask (detail of `E_STATE`, SAF-FW-020)

| Bit | Name | Set when (for the refused command) |
|---|---|---|
| 0 | `ESTOP` | ESTOP latched or E-stop sense input open |
| 1 | `HALT` | HALT latched |
| 2 | `FAULT` | any FAULT latched (§7.6) |
| 3 | `NOT_ENABLED` | motion state NOT_ENABLED (ENABLE never done, or DISABLE / E-stop / idle disable / driver power loss since) |
| 4 | `NOT_HOMED` | MOVE_ABS, MOVE_UNTIL_LOAD or JOG with a bound while not homed |
| 5 | `LIMIT` | motion toward an active or latched limit switch (direction = sign(target − x) or sign(v)) |
| 6 | `AFE_STALE` | no HX711 sample for `afe.timeout_ms` |
| 7 | `AFE_SATURATED` | last HX711 sample at a rail |
| 8 | `DRV_UNPOWERED` | `drv.pwr_sense_enable` and the DRV_POWER input reads "off" (R5 §1.5, D-28) |
| 9 | `DRIVER_ALARM` | ALM active and driver power present (sense disabled → assumed present) (D-28) |
| 10–15 | — | reserved (0) |

ENABLE evaluates only bits 0 and 8. HOME evaluates all bits except 4 and 5 (the homing sequence handles the
switches, FW-HOM-001). JOG 0 is never refused by state.

### 4.4 Check order (defines which NACK wins; FW-CMD-001)
1. **TYPE** defined → else `E_UNKNOWN_CMD`.
2. **LEN** → `E_LENGTH`.
3. **Arguments** (no state needed except the current parameter values, position and load):
   `E_PARAM_ID` → `E_TYPE` → `E_RANGE` (fields in payload order).
4. **State**, in this order: `E_STATE` (BLOCK mask) → `E_BUSY` → `E_CAUSE_ACTIVE` → `E_CONFIRM` → `E_CONFIG`.
5. **Execution**: `E_NVM`, `E_INTERNAL`.

`ref_cmdcheck.py` is the executable form of §4–§6 and `check_vectors.json` its normative test set.

---

## 5. Command semantics

### 5.1 System
**PING** — no action; heartbeat (every valid command frame refreshes the link watchdog, §9.1).

**GET_INFO** — INFO §7.1. First command after opening the port (§9.5).

**GET_STATUS** — STATUS §7.2: current state, latches, inputs and counters (FW-CMD-004). With the stream off it
is the confirmation path for STOP/HALT/PAUSE (§9.4).

**REBOOT** — magic ≠ `0xB007B007` → `E_RANGE` (detail 0); moving → `E_BUSY` 1. The FW sends the response,
drains TX and resets ≤ 50 ms later (boot behaviour §6.4; EVENT BOOT follows).

### 5.2 Parameters and NVM (FW-CFG-001…003, FW-NVM-001…003)
**GET_ALL_PARAMS** — entries in ascending id order; page p holds table indices `20·p … 20·p+19`;
`page_count = ceil(PARAM_COUNT / 20)` (dict_version 1: 48 parameters → 3 pages, 152/152/68 B frames);
`page ≥ page_count` → `E_RANGE` (detail 0). Reading pages 0..page_count−1 returns every parameter exactly
once with its current RAM value (FW-CFG-002).

**GET_PARAM** — unknown id → `E_PARAM_ID`.

**SET_PARAM** — request = PARAM_ENTRY (§7.5). Checks: unknown id → `E_PARAM_ID`; type byte ≠ parameter type →
`E_TYPE`; value outside `[min, max]`, enum code undefined, bool > 1, f32 NaN/Inf, or non-zero padding →
`E_RANGE` (never clamped); parameter without `moving_ok` while moving (motion state MOVE_ABS, JOG,
MOVE_UNTIL_LOAD, HOMING or STOPPING) → `E_BUSY` 1; hard rule (§11.4) violated against the current RAM values →
`E_CONFIG`. On OK the RAM value is replaced and the response returns the value **as stored** (identical to a
following GET_PARAM). Effective no later than 10 ms after the response (load thresholds: from the next
sample, SAF-FW-010), except `reboot_required` parameters (effective after SAVE_PARAMS + REBOOT; STATUS
`sys_flags.REBOOT_PENDING` = 1). **SET_PARAM never writes flash** (FW-CFG-003).

**SAVE_PARAMS** — moving → `E_BUSY` 1. Drains TX, writes the RAM image of all `nvm` parameters to the
alternate NVM record (sequence number + CRC-32 + PARAM_DICT_HASH, FW-NVM-002), verifies, clears CFG_DIRTY,
EVENT PARAMS_SAVED (value = record sequence number). Failure → `E_NVM` 2/3 + EVENT NVM_ERROR, previous record
kept, RAM unchanged. Response ≤ 2.5 s (NFR-008). HX711 conversions missed during the stall are marked by
`OVERRUN` in the next DATA frame (FW-NVM-003). Duration → STATUS `nvm_save_ms`.

**LOAD_PARAMS** — moving → `E_BUSY` 1. Applies boot rules 3–5 (§11.3) to the newest valid record; session
parameters (`nvm: false`) keep their RAM values. No valid record → `E_NVM` 1, RAM unchanged; a record that
violates a hard rule → `E_NVM` 1, RAM unchanged. EVENT PARAMS_LOADED (arg = values replaced by defaults) or,
for a record with another dictionary hash, PARAMS_DEFAULTED (arg 3).

**DEFAULT_PARAMS** — moving → `E_BUSY` 1. RAM = defaults for **all** parameters incl. session values (FW load
thresholds back to ±7 022 271, SAF-FW-010); NVM untouched; EVENT PARAMS_DEFAULTED (arg 0). The SW MUST
re-send its session values afterwards (SAF-SW-002, §11.5).

### 5.3 Streaming and validity (FW-STR-001…005, FW-CMD-002, IF-007)
**STREAM_START / STREAM_STOP** — start/stop DATA frames; idempotent; the stream is **off after boot**.
The stream state never affects a safety function (FW-STR-001). EVENT frames are sent regardless of the
stream state.

While the stream is on: **one DATA frame per HX711 conversion** (IF-007, FW-STR-002), transmission starting
≤ 2 ms after data-ready; `t_us` = 32-bit µs timestamp taken at the DOUT-ready EXTI and `setpoint_um` latched
in the same ISR (FW-AFE-005). While the AFE is stale (`afe.timeout_ms`), **fallback frames** are sent at
`stream.fallback_hz` with `afe_raw = 0x80000000`, status `NO_AFE_DATA` and `AFE_STALE`, `t_us` and
`setpoint_um` taken at frame assembly (FW-STR-005). M1 FW with synthetic samples sets feature
`AFE_SYNTHETIC` (§7.1).

**SET_VALID** — `valid` > 1 → `E_RANGE` 0. Response `t_us` = device time at which the command was processed:
every DATA frame whose `t_us` is **not earlier** than this value (32-bit modular comparison:
`(i32)(t_frame − t_resp) ≥ 0`) carries the new VALID; frames with an earlier `t_us` carry the old value
(FW-CMD-002). Valid also while the stream is off (the value is stored). VALID = 0 at boot, never persisted.
**Automatic clear** (SAF-FW-001): every operational stop of §6.2 (incl. STOP while idle, HALT, PAUSE, button
stops, limit, load limit, AFE fault, link watchdog, step fault, homing failure, E-stop, driver power loss) —
**except the jog dead-man** — clears VALID; if it was 1, EVENT VALID_CLEARED (arg = stop cause §8.2) is sent
and the next DATA frame carries VALID = 0.

### 5.4 Motion (FW-MOT-004…009, SAF-FW-020, SAF-FW-021, FW-HOM-001…002)
Common rules:
- **Absolute arguments only.** Accel argument `0` = `motion.a_max_um_s2`; a non-zero accel above it → `E_RANGE`.
- **Speed cap** (FW-MOT-009, R5 §2.3): `v_limit = min(v_max, floor(motion.max_step_rate_hz · 1000 /
  motion.steps_per_mm))` with `v_max = motion.v_max_load_um_s` for MOVE_UNTIL_LOAD and for any command accepted
  while **loaded** (`abs(raw − safety.zero_raw) ≥ safety.release_band_raw`, or AFE stale), else
  `motion.v_max_travel_um_s`; un-homed JOG additionally ≤ `motion.v_unhomed_um_s`. A speed of 0 (MOVE_ABS,
  MOVE_UNTIL_LOAD) or above `v_limit` → `E_RANGE` (offset of the speed field). STATUS `v_limit_um_s` reports
  the current cap for MOVE_ABS. A `steps_per_mm` change therefore never invalidates a speed parameter.
- Targets/bounds outside `[limits.soft_min_um, limits.soft_max_um]` → `E_RANGE` (offset 0 resp. 8).
- State gating: BLOCK mask (§4.3) → `E_STATE`; ENA settle running → `E_BUSY` 2; any motion running
  (incl. STOPPING) → `E_BUSY` 1 — **except JOG while jogging** (speed/bound change). There is no duplicate
  acknowledgement: motion commands are never auto-retried (§9.3).
- An accepted motion command clears PAUSED (EVENT PAUSE_CLEARED arg 1) and discards nothing else.
- Every motion ends with exactly **one MOVE_DONE** EVENT (reason §8.3, `value` = final position µm,
  `value2` = final step count). A motion ended by a stop source additionally produces EVENT STOPPED (cause)
  before MOVE_DONE (reason STOPPED).

**MOVE_ABS** (`target_um`, `v_um_s`, `a_um_s2`) — requires HOMED. Trapezoid/triangle profile (FW-MOT-003) to
the absolute target; MOVE_DONE reason TARGET. A target equal to the current position completes at once
(MOVE_DONE TARGET, no pulse).

**JOG** (`v_um_s` signed, `a_um_s2`, `bound_um`) — `v ≠ 0`: move in the sign direction at |v| (offset 0
checked against `v_limit`), stopping with a planned deceleration at the end point: `bound_um` if given, else
the soft limit in that direction (homed) or `home.max_travel_um` from the jog's start point (un-homed).
`bound_um = 0x80000000` (`JOG_NO_BOUND`) = no bound; any other value MUST lie inside the soft limits and
strictly ahead of the axis in the jog direction (else `E_RANGE` 8) and requires HOMED (else `E_STATE`
NOT_HOMED) (SW_design F-B-15). A JOG while jogging changes speed, acceleration and bound on the fly (a
reversal decelerates to zero first, DIR setup respected) and refreshes the **dead-man**: without a JOG for
`motion.jog_timeout_ms` the FW performs a controlled stop (STOPPED cause JOG_DEADMAN; VALID unchanged)
(SAF-FW-016). End point reached → MOVE_DONE BOUND or SOFT_LIMIT. **JOG 0** = controlled stop of a running jog
(`motion.a_stop_um_s2`, MOVE_DONE JOG_ZERO); while not jogging it is an OK no-op; it is never refused by state.

**MOVE_UNTIL_LOAD** (`bound_um`, `v_um_s`, `a_um_s2`, `raw_stop`, `cmp`) — requires HOMED. Direction =
sign(`bound_um` − x); `raw_stop` ∈ [−8 388 608, 8 388 607] (offset 12); `cmp` (offset 16): **0 = stop when
raw ≥ raw_stop**, **1 = stop when raw ≤ raw_stop** (the SW chooses `cmp` from the expected sign of the raw
change, i.e. sign(K) and the motion direction; F-B-04). Before the first pulse the last sample is compared:
already beyond → MOVE_DONE LOAD_THRESHOLD without motion (so a repeated command never moves further).
During the move **every** HX711 sample is compared; the first sample beyond `raw_stop` causes an immediate
stop with the load-path timing of SAF-FW-002 → MOVE_DONE LOAD_THRESHOLD. Reaching `bound_um` → planned stop
exactly at the bound → MOVE_DONE BOUND. Fallback frames are not samples (AFE stale while moving =
AFE_FAULT).

**HOME** (`flags`) — bits 1–7 ≠ 0 → `E_RANGE` 0. Load pre-check (SAF-FW-021): `abs(raw − safety.zero_raw) >
home.max_load_raw` and bit0 = 0 → `E_CONFIRM`. Sequence FW-HOM-001 (release if active → fast seek at
`home.v_fast_um_s` → immediate stop at the edge → back-off → slow approach at `home.v_slow_um_s` with edge
capture in the EXTI ISR → set zero → move to 0); homing speeds are limited to the step-rate cap. Zero
definition: `home.ref_switch` START → captured START edge at x = −`home.offset_um`; home switch END → captured END
edge at x = `limits.soft_max_um` + `home.offset_um` (provisional, OI-ICD-01). If the axis was HOMED before,
the captured edge is compared with its expected position; a deviation > `home.drift_tol_um` latches fault
HOME_DRIFT (value = deviation µm) but homing completes (R5 §8). Success → HOMED, POS_UNCERTAIN cleared,
EVENT HOMED (value = deviation µm, 0 if not homed before), MOVE_DONE TARGET at 0. Failures (FW-HOM-002): no
edge within `home.max_travel_um` → fault HOME_NOT_FOUND; the opposite switch reached → fault HOME_WIRING;
any other stop during homing → HOME_FAILED reason ABORTED (not a fault latch; the stopping cause has its own
latch, if any). Every failure: immediate stop, HOMED cleared, EVENT HOME_FAILED, MOVE_DONE STOPPED. Expected
home-switch edges during HOME do not set LIMIT latches.

**ENABLE** — BLOCK bits ESTOP or DRV_UNPOWERED → `E_STATE`. Drives ENA to the enabled level, waits
`motion.ena_settle_ms` (no PUL/DIR edge) and then sets ENABLED (EVENT DRIVER_ENABLED); the response body is
the remaining settle time in ms (0 if already enabled = no-op). (FW-MOT-008, D-28: 500 ms.)

**DISABLE** — moving → `E_BUSY` 1. Drives ENA to the disabled level, clears ENABLED and HOMED, EVENT
DRIVER_DISABLED (arg 1). The SW asks "specimen unloaded?" first (SAF-SW-004).

### 5.5 Stops, pause and clears (FW-MOT-007, SAF-FW-001…006, SAF-FW-022/023, D-14, D-26)
All five commands below are accepted in **every** state and are idempotent.

**STOP** (`mode`) — `mode` > 1 → `E_RANGE` 0. 0 = immediate stop (no further PUL edge ≤ 2 ms after the last
command byte, SAF-FW-002), 1 = controlled stop (`motion.a_stop_um_s2`). Not latched; the running motion
target and ramp state are discarded (SAF-FW-001); VALID cleared (also when idle). EVENT STOPPED (cause
PC_STOP / PC_STOP_CONTROLLED) if a motion was running.

**HALT** — immediate stop + **HALT latch** (`halt_src` = PC unless already latched by the button) + VALID
cleared; EVENT HALT_SET (arg = source) on the first HALT only. Motion refused (`E_STATE` HALT) until
HALT_CLEAR.

**HALT_CLEAR** — if HALT is latched: refused with `E_CAUSE_ACTIVE` while the physical STOP button is active or
released for less than `io.release_ms`; otherwise clears HALT (EVENT HALT_CLEARED). HALT_CLEAR also clears
**PAUSED** (EVENT PAUSE_CLEARED arg 2). No motion restarts (SW-STOP-003). Nothing latched → OK, no effect.

**PAUSE** — the PC equivalent of the physical PAUSE button (Orchestrator decision on F-B-03): while moving →
controlled stop (STOPPED cause PC_PAUSE, MOVE_DONE STOPPED); in every case PAUSED is set (EVENT PAUSED arg =
source PC) and VALID cleared. PAUSE while PAUSED → OK, no event. PAUSED clears on the next **accepted** motion
command or HALT_CLEAR. **Resume is a PC action** (re-issue the absolute target / approach + trim,
SW-STOP-004); the FW never resumes by itself.

**ESTOP_CLEAR** — if ESTOP is latched: refused with `E_CAUSE_ACTIVE` (0xFFFF = input open, else the missing
ms) until the sense input has been closed continuously for `io.estop_release_ms`; otherwise clears ESTOP
(EVENT ESTOP_CLEARED). Afterwards the driver stays disabled and the axis not homed: ENABLE, then HOME, are
required (SAF-FW-006). Nothing latched → OK.

**FAULT_CLEAR** — clears all latched faults whose cause is gone; if any latched fault (other than
LOAD_LIMIT) still has its cause present → `E_CAUSE_ACTIVE` (detail = FAULT mask of those) and **nothing** is
cleared. LOAD_LIMIT is always clearable so the operator can unload; afterwards the FW re-trips immediately if
the violation grows by more than `safety.load_regrow_raw` beyond the value at clear (SAF-FW-011). Causes:
AFE_FAULT — AFE stale or last sample saturated; LIMIT_WIRING — both limit inputs active; K1_WELDED — E-stop
sense open while driver power present; STEP_FAULT, HOME_NOT_FOUND, HOME_WIRING, HOME_DRIFT — none (clearable
at once). OK body = mask of the cleared faults; EVENT FAULT_CLEARED (arg = that mask).

---

## 6. FW state model (SRS §3.2)

### 6.1 Motion state (`motion_state`, STATUS)

| Code | State | Meaning | DATA flags |
|---|---|---|---|
| 0 | NOT_ENABLED | after boot, DISABLE, E-stop, idle disable or driver power loss; no pulse possible | ENABLED = 0 |
| 1 | ENABLING | ENA asserted, `motion.ena_settle_ms` running | ENABLED = 0 |
| 2 | IDLE | enabled, standing (holding) | ENABLED = 1, MOVING = 0 |
| 3 | MOVE_ABS | executing MOVE_ABS | MOVING = 1 |
| 4 | JOG | executing JOG | MOVING = 1 |
| 5 | MOVE_UNTIL_LOAD | executing MOVE_UNTIL_LOAD | MOVING = 1 |
| 6 | HOMING | executing HOME (`home_phase` §7.2) | MOVING = 1 |
| 7 | STOPPING | controlled deceleration in progress | MOVING = 1 |

HOMED is orthogonal (flag). Transitions: ENABLE: NOT_ENABLED → ENABLING → IDLE; motion command: IDLE → 3…6 →
IDLE (MOVE_DONE); controlled stop: 3…6 → STOPPING → IDLE; immediate stop: 3…7 → IDLE; DISABLE / E-stop / idle
disable / driver power loss: any → NOT_ENABLED.

### 6.2 Stop sources, latches and clear conditions (SRS §3.2, SAF-FW-001…023)

| Trigger | Stop | ENA | Latch → clear | HOMED | VALID | EVENTs |
|---|---|---|---|---|---|---|
| E-stop sense opens | immediate (≤ 100 µs) | disabled ≤ 1 ms | ESTOP → ESTOP_CLEAR (input closed ≥ `io.estop_release_ms`), then ENABLE + HOME | cleared | cleared | ESTOP_SET, STOPPED (ESTOP), DRIVER_DISABLED (3) |
| PC STOP | immediate / controlled | kept | none | kept | cleared | STOPPED |
| PC HALT | immediate | kept | HALT (PC) → HALT_CLEAR | kept | cleared | HALT_SET, STOPPED |
| physical STOP/BREAK button | immediate (≤ 200 µs) | kept | HALT (BUTTON) → released ≥ `io.release_ms` + HALT_CLEAR | kept | cleared | STOP_BUTTON, HALT_SET, STOPPED |
| physical PAUSE button / PC PAUSE | controlled | kept | PAUSED → next accepted motion command or HALT_CLEAR; button press while PAUSED → RESUME_REQUEST only | kept | cleared | PAUSE_BUTTON, PAUSED, STOPPED |
| limit switch while moving | immediate (≤ 200 µs) | kept | LIMIT_x → auto: released ≥ `io.release_ms` and moved away | kept | cleared | LIMIT_SET, STOPPED, LIMIT_CLEARED |
| both limit inputs active | immediate | kept | fault LIMIT_WIRING → both released + FAULT_CLEAR | kept | cleared | FAULT_SET, STOPPED |
| FW load limit / rail sample | immediate (≤ 200 µs after DRDY) | kept | fault LOAD_LIMIT → FAULT_CLEAR (always; re-trip on regrow) | kept | cleared | FAULT_SET, STOPPED |
| AFE stale while moving | immediate | kept | fault AFE_FAULT → fresh samples + FAULT_CLEAR | kept | cleared | AFE_STALE, FAULT_SET, STOPPED |
| link watchdog (moving) | controlled | kept | LINK_WDG status → next valid frame | kept | cleared | LINK_WDG, STOPPED, LINK_RESTORED |
| jog dead-man | controlled | kept | none | kept | **unchanged** | STOPPED (JOG_DEADMAN) |
| step overrun / count fault | immediate | kept | fault STEP_FAULT → FAULT_CLEAR | **cleared** | cleared | FAULT_SET, STOPPED |
| homing failure | immediate | kept | fault HOME_NOT_FOUND / HOME_WIRING → FAULT_CLEAR (ABORTED: no latch) | cleared | cleared | HOME_FAILED, STOPPED |
| home drift at re-homing | none (homing completes) | kept | fault HOME_DRIFT → FAULT_CLEAR | set (new zero) | — | FAULT_SET, HOMED |
| E-stop open + driver power still on 100 ms | — | — | fault K1_WELDED → cause gone + FAULT_CLEAR (R5 §1.5) | — | — | FAULT_SET |
| driver power lost (sense enabled) | immediate | disabled | none (state follows the input; ENABLE after power returns) | cleared | cleared | DRIVER_POWER (0), STOPPED, DRIVER_DISABLED (4) |
| idle ≥ `safety.idle_disable_s` and unloaded | — | disabled | none → ENABLE | cleared | — | DRIVER_DISABLED (2) |
| PC DISABLE | refused while moving | disabled | — | cleared | — | DRIVER_DISABLED (1) |
| ALM active | **no stop** (D-16); new motion refused while driver power present (D-28) | kept | — | — | — | ALM_CHANGED |
| MCU reset / IWDG / power-up | PUL idle from reset | left at "no current" (driver enabled, holding; disabled if E-stop open) | boot: NOT_ENABLED | cleared | 0 | BOOT |

Every immediate stop that may have truncated a pulse in flight sets `POS_UNCERTAIN` (±1 step, HOMED kept;
cleared by the next HOME) (SAF-FW-004). A stopped move is never resumed by the FW.

### 6.3 Command acceptance matrix (summary; `check_vectors.json` is normative)
"✓" = accepted; "B" = `E_BUSY`; "S(bit)" = `E_STATE` with that BLOCK bit; "C" = `E_CAUSE_ACTIVE`.

| Command | NOT_ENABLED | ENABLING | IDLE | moving (3–6) | STOPPING | ESTOP latched | HALT latched | FAULT latched | not homed |
|---|---|---|---|---|---|---|---|---|---|
| PING, GET_*, STREAM_*, SET_VALID | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| SET_PARAM | ✓ | ✓ | ✓ | `moving_ok` only, else B | as moving | ✓ | ✓ | ✓ | ✓ |
| SAVE / LOAD / DEFAULT_PARAMS, REBOOT | ✓ | ✓ | ✓ | B | B | ✓ | ✓ | ✓ | ✓ |
| ENABLE | ✓ | ✓ (no-op) | ✓ (no-op) | ✓ (no-op) | ✓ (no-op) | S(ESTOP) | ✓ | ✓ | ✓ |
| DISABLE | ✓ | ✓ | ✓ | B | B | ✓ | ✓ | ✓ | ✓ |
| MOVE_ABS, MOVE_UNTIL_LOAD | S(NOT_ENABLED) | B(2) | ✓ | B | B | S(ESTOP) | S(HALT) | S(FAULT) | S(NOT_HOMED) |
| JOG ≠ 0 | S(NOT_ENABLED) | B(2) | ✓ | JOG: ✓, else B | B | S(ESTOP) | S(HALT) | S(FAULT) | ✓ (≤ v_unhomed, no bound) |
| HOME | S(NOT_ENABLED) | B(2) | ✓ (E_CONFIRM if loaded) | B | B | S(ESTOP) | S(HALT) | S(FAULT) | ✓ |
| JOG 0, STOP, HALT, PAUSE | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| HALT_CLEAR / ESTOP_CLEAR / FAULT_CLEAR | ✓ | ✓ | ✓ | ✓ | ✓ | C while cause active | C while cause active | C while cause active | ✓ |

Additional refusals of motion commands: LIMIT toward an active/latched switch, AFE stale/saturated,
DRV_UNPOWERED, DRIVER_ALARM (§4.3).

### 6.4 Boot (SAF-FW-018, SAF-FW-007, D-13)
PUL idle from reset release; ENA left at the "no current" level (driver enabled, holding) unless the E-stop
input is open (then disabled level); motion state NOT_ENABLED; HOMED = 0; VALID = 0; stream off; inputs
active at boot (E-stop, limits, STOP button) are latched/reported from the first GET_STATUS and refuse motion
accordingly; parameters per §11.3; EVENT BOOT (arg = reset cause) queued.

---

## 7. Structures

### 7.1 INFO (44 bytes) (FW-CFG-004)

| Off | Type | Field | Notes |
|---|---|---|---|
| 0 | u8 | `proto_major` | 1 |
| 1 | u8 | `proto_minor` | 0 |
| 2 | u8 | `payload_version` | 1 |
| 3 | u8[3] | `fw_version` | major, minor, patch |
| 6 | u32 | `param_dict_hash` | PARAM_DICT_HASH compiled into the FW |
| 10 | u8[12] | `uid` | MCU unique ID (F446: 0x1FFF7A10, 96 bit, as read) |
| 22 | char[16] | `build` | build id, e.g. `20261003-1200`, NUL-padded |
| 38 | u16 | `param_count` | number of dictionary entries |
| 40 | u32 | `feature_mask` | bit0 AFE (real HX711), 1 AFE_SYNTHETIC (M1 placeholder samples), 2 MOTION, 3 HOMING, 4 MOVE_UNTIL_LOAD, 5 NVM, 6 TWIN (host twin build), 7 BUTTONS (STOP/PAUSE inputs), 8 DRV_SIGNALS (ALM/PEND/DRV_POWER); others 0. The SW greys out functions whose bit is 0. |

### 7.2 STATUS (84 bytes) (FW-CMD-004, NFR-005, NFR-006, F-B-02)

| Off | Type | Field | Notes |
|---|---|---|---|
| 0 | u32 | `uptime_ms` | since boot |
| 4 | u32 | `t_us` | device time now (PC-device time pairs) |
| 8 | u8 | `flags` | DATA flags (§7.6) as of now |
| 9 | u8 | `motion_state` | §6.1 |
| 10 | u16 | `status` | DATA status bits (§7.6) as of now |
| 12 | u16 | `faults` | latched FAULT mask (§7.6) |
| 14 | u16 | `io` | input/output levels (§7.6) |
| 16 | u8 | `home_phase` | 0 NONE, 1 PRECHECK, 2 RELEASE, 3 FAST_SEEK, 4 BACKOFF, 5 SLOW_APPROACH, 6 MOVE_TO_ZERO, 7 DONE |
| 17 | u8 | `halt_src` | 0 NONE, 1 PC, 2 BUTTON |
| 18 | u8 | `reset_cause` | 0 UNKNOWN, 1 POWER_ON, 2 PIN, 3 SOFTWARE, 4 IWDG, 5 WWDG, 6 LOW_POWER, 7 BROWN_OUT |
| 19 | u8 | `sys_flags` | bit0 CLK_FALLBACK (HSI, FW-PLT-002), 1 CFG_DIRTY (§11.2), 2 STREAM_ON, 3 REBOOT_PENDING, 4 NVM_DEFAULTED (defaults after boot/LOAD rules 2/4/5, until the next SAVE or clean LOAD) |
| 20 | i32 | `pos_um` | commanded position now (µm) |
| 24 | i32 | `target_um` | active end point (MOVE_ABS target, JOG end point, MOVE_UNTIL_LOAD bound); = `pos_um` when idle |
| 28 | i32 | `pos_steps` | step counter (machine zero = 0) |
| 32 | i32 | `afe_raw_last` | last HX711 sample (`0x80000000` if none) |
| 36 | u16 | `afe_rate_dsps` | measured conversion rate, 0.1 SPS (median of 16 periods, FW-AFE-004); 0 = none |
| 38 | u16 | `afe_reinit_count` | HX711 power-down re-initialisations (FW-AFE-003) |
| 40 | u32 | `rx_frames_ok` | CRC-valid frames received |
| 44 | u32 | `rx_crc_errors` | |
| 48 | u32 | `rx_frame_errors` | LEN errors + inter-byte timeouts + invalid TYPE |
| 52 | u32 | `rx_overruns` | UART/DMA RX overflows (R3 P6) |
| 56 | u32 | `tx_drops` | DATA frames dropped in the FW |
| 60 | u16 | `event_overflows` | EVENTs lost by queue overflow |
| 62 | u16 | `loop_max_us` | longest main-loop pass outside NVM operations (NFR-006) |
| 64 | u16 | `link_age_ms` | since the last valid command frame (saturating) |
| 66 | u16 | `stack_free_min` | stack high-water mark, bytes (NFR-005) |
| 68 | u16 | `nvm_save_ms` | duration of the last SAVE (0 = none since boot) |
| 70 | u16 | `idle_disable_left_s` | seconds until idle disable; `0xFFFF` = not counting (loaded, moving, disabled or 0 = never) |
| 72 | u32 | `nvm_record_seq` | sequence number of the active NVM record (0 = none) |
| 76 | u32 | `nvm_save_uptime_ms` | `uptime_ms` at the last SAVE (0 = none since boot) |
| 80 | u32 | `v_limit_um_s` | current MOVE_ABS speed cap (§5.4) |

### 7.3 DATA payload, PAYLOAD_VERSION 1 (18 bytes, frame 26 bytes) (IF-006, D-05, D-23, FW-STR-003)
Field order = D-05 order, then the D-23 additions.

| Off | Type | Field | Meaning |
|---|---|---|---|
| 0 | u32 | `t_us` | device time (1 MHz, wraps every 71.6 min) of the HX711 DOUT-ready EXTI (FW-TIM-001); fallback frames: frame assembly time |
| 4 | u8 | `payload_version` | = 1 |
| 5 | u8 | `flags` | §7.6; **bit 0 = VALID** (D-05) |
| 6 | i32 | `afe_raw` | HX711 24-bit result sign-extended (−8 388 608…8 388 607; rails = saturated); `0x80000000` = no AFE data (fallback frame) |
| 10 | i32 | `setpoint_um` | **setpoint distance** = commanded position (steps issued × 1000 / steps_per_mm, round half away) latched at data-ready (SRS §2, A-03) |
| 14 | u16 | `frame_seq` | §2.2 |
| 16 | u16 | `status` | §7.6 |

The SW unwraps `t_us` (`t < t_prev ⇒ +2³²`); `Δt_us > 1.5 ×` median period ⇒ missed conversion (R4 §9).

### 7.4 EVENT payload (16 bytes, frame 24 bytes) (FW-STR-006)

| Off | Type | Field | Meaning |
|---|---|---|---|
| 0 | u32 | `t_us` | device time of the event |
| 4 | u16 | `code` | §8.1 |
| 6 | u16 | `arg` | per code |
| 8 | i32 | `value` | per code (positions in µm) |
| 12 | i32 | `value2` | per code (step count where a position is given, else 0) |

EVENTs are a notification; the state is authoritative in DATA flags / GET_STATUS. Queue ≥ 8 deep, overflow
counted; EVENTs are sent whether or not the stream is on.

### 7.5 PARAM_ENTRY (7 bytes) and wire values (FW-CFG-002/003)

| Off | Type | Field |
|---|---|---|
| 0 | u16 | `id` (Appendix A) |
| 2 | u8 | `type`: 1 u8, 2 i8, 3 u16, 4 i16, 5 u32, 6 i32, 7 f32, 8 bool, 9 enum |
| 3 | u8[4] | `value`: little-endian in the low `sizeof(type)` bytes, padding bytes = 0; f32 = binary32; bool 0/1; enum = code |

### 7.6 Bit tables
Reserved bits are sent as 0 and ignored by receivers.

**DATA `flags` (u8)**: 0 VALID · 1 MOVING · 2 HOMED · 3 ENABLED · 4 ESTOP (latched or sense input open) ·
5 HALT · 6 FAULT (any FAULT latched) · 7 OVERRUN (≥ 1 DATA frame dropped or ≥ 1 conversion missed by the FW
since the previous sent frame).

**DATA `status` (u16)**: 0 PAUSED · 1 LIMIT_START (input active or latched) · 2 LIMIT_END · 3 LOAD_LIMIT
(latched) · 4 AFE_STALE · 5 AFE_SATURATED (this sample at a rail) · 6 AFE_SETTLING · 7 AFE_RATE_MISMATCH ·
8 LINK_WDG · 9 STOP_BTN (input active) · 10 PAUSE_BTN (input active) · 11 ALM (active) · 12 PEND (active) ·
13 POS_UNCERTAIN · 14 NO_AFE_DATA (fallback frame) · 15 DRV_PWR (driver power present; reads 1 when
`drv.pwr_sense_enable` = false).

A frame belongs to a **steady-state window** (SW-REP-002) only if VALID = 1, MOVING = 0 and none of flags
bits 4–7 and status bits 0–8, 13, 14 is set.

**FAULT mask (u16)** (STATUS `faults`, FAULT_CLEAR body/detail, FAULT_SET arg = bit index): 0 LOAD_LIMIT ·
1 AFE_FAULT · 2 STEP_FAULT · 3 LIMIT_WIRING · 4 HOME_NOT_FOUND · 5 HOME_WIRING · 6 K1_WELDED · 7 HOME_DRIFT.

**IO mask (u16)** (STATUS `io`; "active" after polarity): 0 ESTOP_OPEN · 1 LIMIT_START · 2 LIMIT_END ·
3 STOP_BTN · 4 PAUSE_BTN · 5 ALM · 6 PEND · 7 DRV_PWR (raw sense input "powered") · 8 ENA_DISABLED (ENA
output at the disabled level) · 9 RATE_80 (RATE output high). A limit that is latched but no longer active
= status LIMIT_x set and io LIMIT_x clear.

**sys_flags**: §7.2. **feature_mask**: §7.1. **BLOCK mask**: §4.3.

---

## 8. EVENT codes (FW-STR-006)

### 8.1 Codes

| Code | Name | `arg` | `value` / `value2` |
|---|---|---|---|
| 1 | BOOT | reset cause (§7.2) | 0 / 0 |
| 2 | STOPPED | stop cause (§8.2) | pos_um / pos_steps |
| 3 | MOVE_DONE | reason (§8.3) | final pos_um / pos_steps |
| 4 | ESTOP_SET | 0 | pos_um / pos_steps |
| 5 | ESTOP_CLEARED | 0 | 0 / 0 |
| 6 | HALT_SET | source 1 PC, 2 BUTTON | 0 / 0 |
| 7 | HALT_CLEARED | 0 | 0 / 0 |
| 8 | PAUSED | source 1 PC, 2 BUTTON | 0 / 0 |
| 9 | PAUSE_CLEARED | 1 motion command, 2 HALT_CLEAR | 0 / 0 |
| 10 | RESUME_REQUEST | 0 (PAUSE button pressed while PAUSED) | 0 / 0 |
| 11 | FAULT_SET | FAULT bit index (§7.6) | deciding value: raw (LOAD_LIMIT), deviation µm (HOME_DRIFT), else pos_um / pos_steps |
| 12 | FAULT_CLEARED | FAULT mask cleared | 0 / 0 |
| 13 | LIMIT_SET | 0 START, 1 END | pos_um / pos_steps |
| 14 | LIMIT_CLEARED | 0 START, 1 END | 0 / 0 |
| 15 | LINK_WDG | 0 | 0 / 0 |
| 16 | LINK_RESTORED | 0 | 0 / 0 |
| 17 | VALID_CLEARED | stop cause (§8.2) | 0 / 0 |
| 18 | HOMED | 0 | drift µm vs. the previous zero (0 if not homed before) / 0 |
| 19 | HOME_FAILED | 1 NOT_FOUND, 2 WIRING, 3 ABORTED | pos_um / pos_steps |
| 20 | DRIVER_ENABLED | 0 | 0 / 0 |
| 21 | DRIVER_DISABLED | 1 PC DISABLE, 2 IDLE, 3 ESTOP, 4 DRV_POWER_LOST | 0 / 0 |
| 22 | STOP_BUTTON | 1 pressed, 0 released (debounced) | 0 / 0 |
| 23 | PAUSE_BUTTON | 1 pressed, 0 released | 0 / 0 |
| 24 | ALM_CHANGED | 1 active, 0 inactive | 0 / 0 |
| 25 | AFE_REINIT | re-init count (low 16 bit) | 0 / 0 |
| 26 | AFE_RATE_MISMATCH | 1 set, 0 cleared | measured rate 0.1 SPS / 0 |
| 27 | AFE_STALE | 1 stale, 0 fresh again | 0 / 0 |
| 28 | PARAMS_SAVED | 0 | record sequence number / 0 |
| 29 | PARAMS_LOADED | values replaced by defaults | 0 / 0 |
| 30 | PARAMS_DEFAULTED | 0 command, 1 no record, 2 CRC error, 3 migration, 4 hard rule | 0 / 0 |
| 31 | NVM_ERROR | E_NVM detail | 0 / 0 |
| 32 | CLK_FALLBACK | 0 | 0 / 0 |
| 33 | DRIVER_POWER | 1 power present, 0 lost | 0 / 0 |
| 34 | NOT_SETTLED | 0 (PEND not active within `drv.pend_timeout_ms` after the last pulse; warning only) | elapsed ms / 0 |

### 8.2 Stop causes (STOPPED arg, VALID_CLEARED arg)
0 NONE · 1 PC_STOP · 2 PC_STOP_CONTROLLED · 3 PC_HALT · 4 STOP_BUTTON · 5 PAUSE_BUTTON · 6 PC_PAUSE · 7 ESTOP ·
8 LIMIT_START · 9 LIMIT_END · 10 LIMIT_WIRING · 11 LOAD_LIMIT (incl. rail) · 12 AFE_FAULT · 13 LINK_WDG ·
14 JOG_DEADMAN · 15 STEP_FAULT · 16 HOME_FAIL · 17 DRV_POWER_LOST.

### 8.3 MOVE_DONE reasons
0 TARGET (MOVE_ABS / HOME end) · 1 LOAD_THRESHOLD (MOVE_UNTIL_LOAD) · 2 BOUND (MOVE_UNTIL_LOAD or JOG bound) ·
3 SOFT_LIMIT (JOG end at a soft limit or un-homed travel bound) · 4 JOG_ZERO · 5 STOPPED (ended by a stop
source; see the preceding STOPPED / HOME_FAILED).

---

## 9. Link supervision and timing (IF-005, IF-011, SAF-FW-015, SAF-SW-003)

### 9.1 FW side (normative)
- **Link watchdog** (SAF-FW-015, D-15): while moving (motion states 3–7), no valid command frame (§3.1) for
  `safety.link_timeout_ms` (default 1000 ms) → controlled stop (driver kept enabled), LINK_WDG set, VALID
  cleared, EVENTs LINK_WDG + STOPPED; LINK_WDG clears at the next valid command frame (EVENT LINK_RESTORED);
  no motion restarts. Checked at ≥ 1 kHz (deceleration starts ≤ timeout + 2 ms).
- **Response time**: ≤ 10 ms from the last command byte to the first response byte; SAVE/LOAD/DEFAULT_PARAMS
  ≤ 2.5 s (NFR-008). STOP/HALT: no further PUL edge ≤ 2 ms after the last command byte (SAF-FW-002).
- **Input capacity**: bursts of up to 4 commands back-to-back (RX DMA ring ≥ 1 KB, overflow counted).
- Time intervals scale with the clock: on the HSI fallback (`CLK_FALLBACK`) up to ±1 %.

### 9.2 SW side: heartbeat (SAF-SW-003)
While connected the SW sends PING whenever nothing else was sent for **150–200 ms**, so the gap between two
command frames never exceeds **250 ms**, and only while its receive/processing pipeline is alive (a dead PC
backend must let the FW watchdog trip). With the 1000 ms default ≥ 3 heartbeats may be lost;
`safety.link_timeout_ms` < 750 ms is not robust (GUI warning).

### 9.3 SW side: timeouts and retries (per command, F-B-01)

| Class | Commands | Rule |
|---|---|---|
| RETRY | PING, GET_INFO, GET_STATUS, GET_PARAM, GET_ALL_PARAMS, SET_PARAM, STREAM_START, STREAM_STOP, SET_VALID, JOG 0 | response timeout 100 ms (GET_INFO at connect 200 ms); ≤ 2 retries, each with a **new SEQ** and the **newest** value the SW wants at that moment (latest wins; a retry superseded by a newer command is dropped); a late response to an abandoned SEQ is ignored |
| CONFIRM | STOP, HALT, PAUSE | priority path; repeated every 50 ms until confirmed — STOP: ACK or `MOVING = 0`; HALT: ACK or HALT flag; PAUSE: ACK or PAUSED — ≤ 20 attempts in 1 s; works with the stream off (§9.4) |
| ONCE_PRIORITY | HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR | priority path, ≤ 2 retries (idempotent clears); NACK detail shown verbatim |
| VERIFY | MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0, ENABLE, DISABLE, SAVE/LOAD/DEFAULT_PARAMS, REBOOT | **never retried automatically.** After a timeout (100 ms; SAVE/LOAD/DEFAULT 3000 ms + wire time of the TX backlog; REBOOT: reconnect after EVENT BOOT or 3 s) the SW sends GET_STATUS and decides from `motion_state`, `target_um`, latches, `sys_flags.CFG_DIRTY` / `nvm_record_seq` whether the command was executed. JOG ≠ 0 refreshes are a stream of new commands every ≤ 100 ms (newest speed); a lost one is covered by the next refresh and by the FW dead-man. |

Why motion is never retried: a retry after a lost response could be executed after an intervening PAUSE-button
stop and would clear PAUSED and move (SAF-FW-023); absolute arguments make an accidental duplicate harmless
(the FW answers `E_BUSY`, or a completed move to the same target is zero-length), but the SW does not rely on
it. Outstanding commands ≤ 4 (priority path exempt); ≤ 100 command frames/s.

**Link states** (SW): DEGRADED after 3 consecutive command timeouts or 300 ms without any received frame while
streaming; LOST after 1 s without frames; during motion, no DATA for > 500 ms → STOP, sequence terminated,
"LINK LOST" (SAF-SW-003). The SW never re-enables, re-homes or restarts motion automatically.

### 9.4 Confirming STOP / HALT / PAUSE with the stream off (F-B-05, SW-STOP-002)
The ACK proves reception and execution (the stop is executed before the response is sent). Without an ACK,
the SW keeps repeating (§9.3) and polls GET_STATUS: `flags.MOVING = 0` (STOP), `flags.HALT` (HALT),
`status.PAUSED` (PAUSE). If neither arrives within 1 s the SW alarms "use the physical STOP / E-stop"; the
FW link watchdog stops a motion whose PC went silent.

### 9.5 Connect sequence and version negotiation (IF-008, SW-PLT-003)
1. Open the port; feed pending bytes to a fresh parser (stale responses have no pending SEQ and are ignored,
   R3 P13); randomise the first SEQ.
2. GET_INFO (200 ms timeout) repeated for ≤ 3 s; heartbeat from the first answer.
3. `proto_major` ≠ SW major or `payload_version` ≠ SW → **read-only**: no motion, no clears-and-enable, no
   config writes; monitoring allowed. `proto_minor` lower → features beyond it unused.
   `param_dict_hash` ≠ SW `PARAM_DICT_HASH` → configuration read-only + warning.
4. GET_STATUS (latches, inputs, reset cause, CFG_DIRTY, NVM_DEFAULTED), GET_ALL_PARAMS (all pages).
5. Write the session values (`safety.zero_raw`, `safety.load_raw_min/max`, SAF-SW-002) and verify by read-back;
   motion stays disabled in the SW until verified.
6. STREAM_START if requested.

---

## 10. Bandwidth (IF-011)
921 600 Bd 8N1 = 92 160 B/s.

| Traffic (FW → PC) | Size | Rate | B/s | % of link |
|---|---|---|---|---|
| DATA | 26 B (282 µs on the wire) | 80 Hz | 2 080 | 2.26 % |
| EVENT | 24 B | ≤ 20 /s (bursts at stops) | ≤ 480 | 0.52 % |
| GET_STATUS response | 93 B | ≤ 5 Hz polling | ≤ 465 | 0.50 % |
| other responses (PING, JOG, SET_…) | 9–16 B | ≤ 20 /s | ≤ 320 | 0.35 % |
| **Total** | | | **≤ 3 345** | **≤ 3.6 %** (requirement ≤ 10 %) |

Fallback frames (10 Hz) replace DATA frames while stale. GET_ALL_PARAMS at connect: 152 + 152 + 68 B once.
PC → FW: JOG refresh 20 B × 10 Hz + PING 8 B × ≤ 7 Hz + commands ≈ 0.4 % of the link. STOP/HALT never wait
behind DATA in the FW (responses have TX priority, §2.4) nor behind SW queues (§2.4).

---

## 11. Parameters (FW-CFG-001…003, FW-NVM-001/002, FW-PAR-001…006)

### 11.1 Dictionary and hash
`00_System/specs/params.yaml` is the single source of truth (schema in its header): id (stable, never
reused), key, type, unit, min/max/default, flags **M** `moving_ok`, **N** `nvm`, **R** `reboot_required`.
`gen_params.py` generates `02_FW/src/gen/params_gen.{h,c}`, `03_SW/src/bend_stand/core/params_gen.py` and
Appendix A; `--check` fails on stale outputs (IF-010). **PARAM_DICT_HASH** = CRC-32 (zlib) of the canonical
serialization (params.yaml header); labels/descriptions are not hashed.

### 11.2 CFG_DIRTY (normative, the only definition; FW-NVM-001)
`sys_flags.CFG_DIRTY` = 1 **iff** there is no valid NVM record, or the record's PARAM_DICT_HASH differs from
the FW's, or any `nvm` parameter's RAM value differs from the record. Session parameters (`nvm: false`) never
affect it. Re-evaluated after every SET_PARAM, SAVE, LOAD and DEFAULT.

### 11.3 NVM records, boot and migration (FW-NVM-002)
Two alternating flash records, each with sequence number, CRC-32, dict_version and PARAM_DICT_HASH, holding
`(id, type, value)` entries of all `nvm` parameters (layout: FW design). A power loss during SAVE leaves the
previous record valid. Boot (and LOAD steps 3–5):
1. — (no factory gesture on this board).
2. No valid record (blank or both CRC-bad) → defaults, CFG_DIRTY = 1, NVM_DEFAULTED, EVENT PARAMS_DEFAULTED
   (1 empty / 2 CRC).
3. Newest valid record with equal hash → loaded; out-of-range values → defaults (counted in
   PARAMS_LOADED arg).
4. Different hash → **migration by id**: entries whose id exists with the same type and an in-range value
   are kept, everything else defaulted; CFG_DIRTY = 1, NVM_DEFAULTED, PARAMS_DEFAULTED (3). Safe because an
   id never changes type, unit or meaning.
5. The resulting image violates a hard rule → all defaults (boot) / `E_NVM` 1 and RAM unchanged (LOAD);
   PARAMS_DEFAULTED (4).
Session parameters always start at their defaults. Flash erase/program only while idle (FW-NVM-003).

### 11.4 Hard rules (SET_PARAM → `E_CONFIG`, detail = id of the other parameter)

| Rule | Condition (must hold after the SET) | Detail for a SET of … |
|---|---|---|
| H1 | `limits.soft_min_um < limits.soft_max_um` | min → id of max; max → id of min |
| H2 | `safety.load_raw_min < safety.load_raw_max` | min → id of max; max → id of min |
| H3 | `1e9 / motion.max_step_rate_hz ≥ motion.pulse_high_ns + motion.pulse_low_min_ns` (integer form: `rate · (high + low) ≤ 1e9`) | rate → id of `pulse_high_ns`; high or low → id of `max_step_rate_hz` |
| H4 | `motion.v_max_load_um_s ≤ motion.v_max_travel_um_s` | load → id of travel; travel → id of load |

Because rules couple parameters, the SW writes a configuration in an order that keeps every rule true after
each single SET (for min/max pairs: move the outward bound first; for H3: lower the rate before widening
pulses, widen the rate after narrowing pulses); an `E_CONFIG` item is retried after the others in a further
pass, until a pass makes no progress (then REJECTED). Some range ends are unreachable by construction
(`soft_min_um = 400 000`, `load_raw_max = −7 151 121`, `load_raw_min = +7 151 121`) — vectors show `E_CONFIG`.

### 11.5 Session values
`safety.zero_raw`, `safety.load_raw_min`, `safety.load_raw_max` are `nvm: false`: the SW computes and writes
them after connect, calibration, tare, LOAD and DEFAULT (SAF-SW-002) and verifies by read-back; without a PC
the FW runs on the safe defaults (±110 % FS − 1 % FS, zero 0).

---

## 12. Test vectors and tools (IF-003, IF-004, IF-010)
- `00_System/tools/ref_codec.py` — pure-Python reference codec: CRC, frame encoder, §2.3 parser, every
  request/response/async payload. `ref_cmdcheck.py` — reference acceptance model of §4–§6.
- `00_System/tools/gen_vectors.py` — **the single vector generator**; `--check` in CI.
  - `vectors/protocol_vectors.json`: CRC vectors; frames (hex + decoded) for every command request and OK
    response, NACKs of every status code, GET_INFO (+ longer variant), GET_STATUS, GET_ALL_PARAMS pages with
    the complete default table, DATA (typical, idle, fallback, both rails, sync pattern in payload, all bits,
    wrap, E-stop), one EVENT per code, a 160-byte (maximum) frame, a bad-CRC frame; parser `streams` (bad CRC,
    noise, split chunks, LEN 0xFFFF and 161, sync in payload good/corrupted, truncated + stream, two frames + split,
    truncated + idle, lone A5 + idle).
  - `vectors/check_vectors.json`: state snapshot + request → expected STATUS/detail (+ exact NACK bytes):
    check order, one vector per motion refusal reason (SAF-FW-020), clears, NVM, and per parameter
    default/min/max/below/above/NaN/type/padding/while-moving (FW-CFG-003), hard rules H1–H4.
- `00_System/tools/gen_params.py` — dictionary generator (§11.1).
- FW host tests and SW pytest MUST (a) decode every vector frame to `decoded`, (b) re-encode `decoded` to the
  identical bytes (or `canonical_payload_hex` where `reencode` = false), (c) reproduce the `streams`
  expectations and counters, (d) for each check vector, answer the request in the given state with the
  expected verdict and NACK bytes without side effects. How each side consumes them: `tools/README.md`.

## 13. Requirement coverage

| Requirement | ICD section |
|---|---|
| IF-001 | header, §0.2, §15 |
| IF-002 | §1, §0.1 |
| IF-003 | §2, §2.3, §12 |
| IF-004 | §2.1 |
| IF-005 | §4, §9.3 |
| IF-006 / IF-007 | §7.3, §5.3 |
| IF-008 | §0.2, §7.1, §9.5 |
| IF-009 | §0.1, §3.2, §5.4 |
| IF-010 | §11.1, §12 |
| IF-011 | §2.4, §10 |
| IF-012 | §3.2 (+ PAUSE) |
| FW-CFG-001…004 | §5.2, §7.1, §11 |
| FW-NVM-001…003 | §5.2, §11.2, §11.3 |
| FW-CMD-001 / 002 / 003 / 004 | §4 / §5.3 / §5.5 / §7.2 |
| FW-STR-001…006, FW-TIM-001 | §5.3, §2.2, §7.3, §7.6, §8 |
| FW-MOT-004…009, FW-HOM-001/002 | §5.4 |
| FW-PAR-001…006, SRS Table 5.1 | Appendix A, `params.yaml` |
| SAF-FW-001…023 (command/flag/event side) | §4.3, §5.4, §5.5, §6, §8 |
| SRS §3.2 | §6.2 |

## 14. Open issues and SRS deltas

| ID | Item | Owner / proposal |
|---|---|---|
| SD-01 | **PAUSE command** added to IF-012 (Orchestrator decision on F-B-03): PAUSED clears on the next accepted motion command or HALT_CLEAR. | Orchestrator: SRS v0.3 (IF-012, §3.2 row "GUI Pause", SAF-FW-023). |
| SD-02 | R5 / D-28 interface items: status DRV_PWR, faults K1_WELDED + HOME_DRIFT, BLOCK bits DRV_UNPOWERED + DRIVER_ALARM, EVENT DRIVER_POWER / NOT_SETTLED, stop cause DRV_POWER_LOST; parameters `drv.pend_timeout_ms`, `drv.pwr_sense_enable`, `home.drift_tol_um`; `motion.ena_settle_ms` default 500. | Orchestrator: SRS v0.3 (§3.2 rows, FW-SW-004, FW-PAR-002/006, Table 5.1). |
| SD-03 | `motion.v_max_um_s` (Table 5.1) replaced by `motion.v_max_travel_um_s` (30 mm/s) + `motion.v_max_load_um_s` (20 mm/s) with hard rule H4 (R5 §2.3, D-28). | Orchestrator: Table 5.1, FW-PAR-003, FW-MOT-009. |
| SD-04 | Renames: `input.release_ms` → `io.release_ms`, `input.estop_release_ms` → `io.estop_release_ms`, `home.switch` → `home.ref_switch` (`switch` is a C keyword). | Orchestrator: Table 5.1 (names provisional per SRS notation). |
| SD-05 | Session parameters `safety.zero_raw`, `safety.load_raw_min/max` are not persisted (`nvm: false`). | Orchestrator: confirm (SAF-SW-002 rewrites them at connect). |
| SD-06 | `motion.pul_invert`, `motion.ena_invert`, `drv.pwr_sense_enable` are `reboot_required` (a live change could emit a step edge or release a holding driver). | Orchestrator: FW-MOT-001 note. |
| SD-07 | JOG carries an optional absolute `bound_um` (F-B-15); "no bound" is `0x80000000`, not 0 (0 can be a valid position when `soft_min_um ≤ 0`). MOVE_UNTIL_LOAD direction is implicit in the absolute `bound_um`; the comparison sense is the explicit `cmp` field. | Orchestrator: FW-MOT-005/006 wording. |
| SD-08 | Motion commands are never auto-retried (VERIFY class, F-B-01); the FW has no duplicate acknowledgement. | Orchestrator: IF-005 wording ("retries only idempotent commands"). |
| SD-09 | FW_design inputs: `motion.max_step_rate_hz` max **100 kHz** (SRS cap 200 kHz, OI-FW-04); `motion.steps_per_mm` min **100** (SRS proposed 1, OI-FW-05; SRS minimum range 100…10 000 still met); MAX_LEN 160 / 20 parameters per page (OI-FW-02). | Orchestrator: FW-MOT-001, FW-MOT-009, Table 5.1. |
| SD-10 | CLK_FALLBACK (requested as a DATA status bit, OI-FW-11) is reported in GET_STATUS `sys_flags` and EVENT CLK_FALLBACK only: DATA `status` has no free bit and the condition is fixed from boot. | Orchestrator / A: accept. |
| OI-ICD-01 | Homing at the END switch: SRS §2 defines the machine coordinate from START only; ICD v0.1 provisionally puts the END edge at `soft_max_um + offset_um`. | Orchestrator / PO: confirm or restrict `home.ref_switch` to START. |
| OI-ICD-02 | Count-based defaults/ranges depend on the nominal 32 212 counts/kg (OI-07). | Re-derive after the first calibration (dict_version bump). |
| OI-ICD-03 | `HOME_ABORTED` (FW-HOM-002) is reported as HOME_FAILED reason ABORTED, not as a latched fault (the stopping cause has its own latch). | Orchestrator: confirm. |

## 15. Change history

| ICD | Date | PROTO / PAYLOAD / dict | Change |
|---|---|---|---|
| 0.1 | 2026-10-03 | 1.0 / 1 / 1 | Initial version from SRS v0.2, D-01…D-28, R3 §1.6, R4 §9, R5 §8, SW_design F-B-01…06/15/19, FW_design OI-FW-02/04/05/11: framing/CRC/parser (Thrust_Stand origin, MAX_LEN 160), 25 commands incl. PAUSE, DATA v1 18 B, EVENT 16 B, STATUS 84 B, INFO 44 B, BLOCK/FAULT/IO masks, state model, retry classes, 48-parameter dictionary, vectors. |

---

## Appendix A. Parameter dictionary (informative — generated; normative source: `params.yaml`)

<!-- BEGIN GENERATED PARAM TABLE (gen_params.py) -->

Generated from `params.yaml` dict_version 1 — **PARAM_DICT_HASH = 0x13961802**, 48 parameters. Flags: **M** = moving_ok (settable while moving), **N** = nvm (persisted), **R** = reboot_required. Normative descriptions: `params.yaml`.

| ID | Key | Type | Unit | Min | Max | Default | Flags | Values / notes |
|---|---|---|---|---|---|---|---|---|
| 0x0101 | `afe.gain_channel` | enum |  |  |  | A128 | N | 0=A128, 1=B32, 2=A64 |
| 0x0102 | `afe.rate_sps` | enum |  |  |  | SPS80 | N | 0=SPS10, 1=SPS80 |
| 0x0103 | `afe.rate_tol_pct` | u8 | % | 5 | 50 | 20 | MN |  |
| 0x0104 | `afe.settle_discard` | u8 | samples | 0 | 20 | 4 | MN |  |
| 0x0105 | `afe.timeout_ms` | u16 | ms | 20 | 1000 | 100 | MN |  |
| 0x0201 | `motion.steps_per_mm` | f32 | steps/mm | 100 | 100000 | 160 | N |  |
| 0x0202 | `motion.pul_invert` | bool |  |  |  | false | NR |  |
| 0x0203 | `motion.dir_invert` | bool |  |  |  | false | N |  |
| 0x0204 | `motion.ena_invert` | bool |  |  |  | false | NR |  |
| 0x0205 | `motion.pulse_high_ns` | u32 | ns | 2500 | 100000 | 10000 | N |  |
| 0x0206 | `motion.pulse_low_min_ns` | u32 | ns | 2500 | 100000 | 10000 | N |  |
| 0x0207 | `motion.max_step_rate_hz` | u32 | Hz | 100 | 100000 | 50000 | N |  |
| 0x0208 | `motion.dir_setup_us` | u16 | us | 5 | 1000 | 20 | N |  |
| 0x0209 | `motion.ena_settle_ms` | u16 | ms | 0 | 2000 | 500 | N |  |
| 0x020A | `motion.v_max_travel_um_s` | u32 | um/s | 1 | 250000 | 30000 | N |  |
| 0x020B | `motion.v_max_load_um_s` | u32 | um/s | 1 | 250000 | 20000 | N |  |
| 0x020C | `motion.a_max_um_s2` | u32 | um/s2 | 1000 | 10000000 | 100000 | N |  |
| 0x020D | `motion.a_stop_um_s2` | u32 | um/s2 | 10000 | 10000000 | 1000000 | N |  |
| 0x020E | `motion.v_unhomed_um_s` | u32 | um/s | 1 | 20000 | 2000 | N |  |
| 0x020F | `motion.jog_timeout_ms` | u16 | ms | 50 | 1000 | 250 | MN |  |
| 0x0301 | `limits.soft_min_um` | i32 | um | -10000 | 400000 | 500 | N |  |
| 0x0302 | `limits.soft_max_um` | i32 | um | 0 | 400000 | 290000 | N |  |
| 0x0401 | `home.ref_switch` | enum |  |  |  | START | N | 0=START, 1=END |
| 0x0402 | `home.v_fast_um_s` | u32 | um/s | 10 | 20000 | 5000 | N |  |
| 0x0403 | `home.v_slow_um_s` | u32 | um/s | 10 | 20000 | 500 | N |  |
| 0x0404 | `home.a_um_s2` | u32 | um/s2 | 1000 | 1000000 | 50000 | N |  |
| 0x0405 | `home.backoff_um` | u32 | um | 0 | 20000 | 2000 | N |  |
| 0x0406 | `home.offset_um` | u32 | um | 0 | 20000 | 1000 | N |  |
| 0x0407 | `home.max_travel_um` | u32 | um | 1000 | 500000 | 360000 | N |  |
| 0x0408 | `home.max_load_raw` | i32 | counts | 0 | 7151121 | 322123 | N |  |
| 0x0409 | `home.drift_tol_um` | u32 | um | 10 | 10000 | 200 | N |  |
| 0x0501 | `safety.load_raw_max` | i32 | counts | -7151121 | 7151121 | 7022271 | M | session value (not persisted) |
| 0x0502 | `safety.load_raw_min` | i32 | counts | -7151121 | 7151121 | -7022271 | M | session value (not persisted) |
| 0x0503 | `safety.load_trip_samples` | u8 | samples | 1 | 4 | 1 | MN |  |
| 0x0504 | `safety.load_regrow_raw` | i32 | counts | 0 | 1288490 | 128849 | MN |  |
| 0x0505 | `safety.zero_raw` | i32 | counts | -8388608 | 8388607 | 0 | M | session value (not persisted) |
| 0x0506 | `safety.release_band_raw` | i32 | counts | 0 | 644245 | 128849 | MN |  |
| 0x0507 | `safety.idle_disable_s` | u16 | s | 0 | 65535 | 600 | MN |  |
| 0x0508 | `safety.link_timeout_ms` | u16 | ms | 200 | 5000 | 1000 | MN |  |
| 0x0601 | `io.release_ms` | u8 | ms | 5 | 200 | 20 | N |  |
| 0x0602 | `io.estop_release_ms` | u16 | ms | 50 | 2000 | 100 | N |  |
| 0x0603 | `io.stop_active_level` | enum |  |  |  | OPEN_ACTIVE | N | 0=OPEN_ACTIVE, 1=CLOSED_ACTIVE |
| 0x0604 | `io.pause_active_level` | enum |  |  |  | CLOSED_ACTIVE | N | 0=OPEN_ACTIVE, 1=CLOSED_ACTIVE |
| 0x0701 | `drv.alm_active_level` | enum |  |  |  | HIGH_ACTIVE | N | 0=HIGH_ACTIVE, 1=LOW_ACTIVE |
| 0x0702 | `drv.pend_active_level` | enum |  |  |  | HIGH_ACTIVE | N | 0=HIGH_ACTIVE, 1=LOW_ACTIVE |
| 0x0703 | `drv.pend_timeout_ms` | u16 | ms | 0 | 5000 | 200 | MN |  |
| 0x0704 | `drv.pwr_sense_enable` | bool |  |  |  | true | NR |  |
| 0x0801 | `stream.fallback_hz` | u8 | Hz | 1 | 80 | 10 | MN |  |

<!-- END GENERATED PARAM TABLE -->
