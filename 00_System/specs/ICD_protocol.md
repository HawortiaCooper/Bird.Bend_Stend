# Bird Bend Stand — Interface Control Document: PC ⇄ FW protocol

| Doc | ICD_protocol |
|---|---|
| Version | **0.7.4 — D-47: link watchdog clears VALID in every motion state; M4 twin vocabulary (specimen)** (change history §15) |
| Date | 2026-10-03 |
| Owner | Implementer C — Integrator (changes only with a version bump + change-history entry, IF-001) |
| Implements | SRS v0.6: IF-001…IF-012, FW-CFG-001…004, FW-NVM-001…003, FW-CMD-001…004, FW-STR-001…006, FW-TIM-001, FW-PAR-001…006 (Table 5.1), command semantics of SAF-FW-001…026 and SRS §3.2; decisions D-03, D-05, D-12…D-31, D-33, D-34, D-36, D-37, D-40…D-43; FW_test_plan v0.3 §6.4 / §8.4 (REQ-C-M2-01…11); SRS v0.3 cross-check (§14 OI-ICD-06); SRS deltas from R5 §8 / D-28 and SW_design F-B-01…06/15/19 (§14); findings GF-01, GF-08 (SW_design_GUI), OI-FW-06/07/11/17…23 (FW_design), F-B-25/28/30 (SW_design), DEF-P1-01…03, OBS-P1-15 (FW_test_plan), SWD-P1-02/15 (SW_test_plan) |
| Protocol | **PROTO_VERSION 1.0**, **PAYLOAD_VERSION 1**, dictionary `params.yaml` dict_version 6 (hash in Appendix A) |
| Machine-readable companions | `00_System/specs/protocol.yaml` (names and codes, §0.3, Appendix B), `00_System/specs/params.yaml` (parameters, Appendix A), `00_System/tools/ref_codec.py` (codec oracle), `ref_cmdcheck.py` (acceptance oracle), `vectors/protocol_vectors.json`, `vectors/check_vectors.json`, `vectors/units_vectors.json`, `vectors/motion_vectors.json`, `vectors/loadlim_vectors.json`, `vectors/linkwdg_vectors.json` (§12) |
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
- **µm ↔ steps (normative, SYS-003, OI-FW-20):** with `spm` = the binary32 value of `motion.steps_per_mm`,
  evaluated in IEEE-754 binary64 left to right: `steps = round(um · spm / 1000)`,
  `um = round(steps · 1000 / spm)` (round = half away from zero, C99 `round()`), speed cap
  `floor(max_step_rate_hz · 1000 / spm)` (§5.4). Results **saturate**: µm and steps to the int32 range
  [−2³¹, 2³¹−1], the speed cap to [0, 2³²−1] (OBS-M1-05; not reachable with the parameter ranges, defined
  for completeness). Any other arithmetic (float32, fixed point) MUST reproduce
  `vectors/units_vectors.json` exactly (tie and saturation cases included).
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

### 0.3 Names and codes: single source (GF-08, OI-FW-11)
- Every protocol name and code — command TYPEs and request LENs, STATUS (NACK) codes and their detail
  enums, BLOCK / DATA flags / DATA status / FAULT / IO / sys_flags / feature bits, motion state, EVENT codes
  and every EVENT argument enum, the SW retry class — is defined **once** in
  `00_System/specs/protocol.yaml` (owner: Integrator).
- `00_System/tools/gen_params.py` (which runs `gen_protocol.py`) generates from it
  `02_FW/src/gen/proto_gen.h` (C `#define`s / enums), `03_SW/src/bend_stand/core/protocol_gen.py`
  (`IntEnum` / `IntFlag`, bit-name tuples `<ID>_BITS` in bit order, descriptions `<ID>_DESC`) and the
  tables of this document marked `GENERATED protocol:<id>` (plus Appendix B). `--check` fails on stale
  outputs.
- FW, SW and GUI MUST use the generated identifiers (no hand-listed codes, bit tables or bit names); the
  GUI channel tree and the SW indicator registry iterate the generated `<ID>_BITS` tuples. Only the
  reference codec `ref_codec.py` keeps hand-written tables, as an independent oracle; a tools test proves
  they equal `protocol.yaml`.
- A new name/code is added to `protocol.yaml` (reserved positions only, §0.2), the generator is run, and
  the ICD version is bumped.

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
GET_ALL_PARAMS response page, 152 bytes. STOP (9 B), HALT (8 B) and PAUSE (8 B) are short fixed-length frames
with fixed TYPEs: the FW 1 kHz **stop sniffer** detects them in the RX ring (CRC-checked) and starts the stop
ahead of normal command processing; the command itself is then processed in order (response, latch,
events) (FW_design §5.9.3, OI-FW-11/19). The sniffed set is `protocol.yaml` `sniffed` (C
`CMD_IS_SNIFFED()`, Python `CMD_SNIFFED`). RESUME, the clears and all other commands are **not** sniffed.

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
- **FW wire order** (OI-FW-18): at every frame boundary the next frame is taken from the class queues in the
  order **DATA > responses > EVENT**. A DATA frame (26 B, 282 µs) therefore never waits behind more than the
  one frame already on the wire (FW-STR-002 ≤ 2 ms), and a response waits at most one DATA frame plus the frame
  on the wire (≤ 2.1 ms, within the 10 ms response time). Stops are executed before their response is queued
  (§9.4), so this order never delays a stop.
- **Drop order** when a class queue is full: DATA first (counted in `tx_drops`, `frame_seq` still advances,
  the next sent frame has `OVERRUN`), then EVENT (counted in `event_overflows`, EVENT SEQ gap); **responses
  are never dropped**.
- SW: STOP, HALT, PAUSE and the clear commands are written through a priority path that bypasses every SW
  queue, lane, rate limit and outstanding-command limit; the longest wait is one frame already being written
  (IF-011, NFR-002/003). RESUME is **not** on the priority path: it is sent on the normal (CONTROL) lane in
  order with the motion commands that follow it (D-37c).
- **SAVE exemption (D-37a, OBS-M1-01):** a SAVE_PARAMS (idle only) stalls the CPU for the flash erase +
  program (≈ 0.5 s with an erase). Command frames received meanwhile are buffered in the RX ring and answered
  **after** the flash operation; this exempts them from the 10 ms response time (§9.1) and, for STOP/HALT/PAUSE,
  from the stop-path budget — acceptable because no motion is possible during a SAVE. The SW therefore sends
  **no non-stop command while a SAVE is outstanding** (until its response or the VERIFY resolution, §9.3);
  stop-class frames (STOP, HALT, PAUSE) stay allowed and are executed right after the flash operation, so the
  RX ring (2 KB) cannot overflow.

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

<!-- BEGIN GENERATED protocol:commands (gen_protocol.py) -->

Generated from `protocol.yaml` `commands` (C `CMD_*`, `CMD_REQ_LEN_*`; Python `Cmd`, `CMD_REQ_LEN`, `CMD_RETRY`).

| TYPE | Name | Request payload (LEN) | Response body on OK | Retry | SW priority path / FW sniffer | SRS |
|---|---|---|---|---|---|---|
| `0x01` | PING | – (0) | – | RETRY | – | SAF-SW-003, SAF-FW-015 |
| `0x02` | GET_INFO | – (0) | INFO §7.1 (44) | RETRY | – | FW-CFG-004, IF-008 |
| `0x03` | GET_STATUS | – (0) | STATUS §7.2 (86) | RETRY | – | FW-CMD-004 |
| `0x04` | REBOOT | `u32 magic = 0xB007B007` (4) | – | VERIFY | – | SAF-FW-018 |
| `0x10` | GET_ALL_PARAMS | `u8 page` (1) | `u8 page, u8 page_count, u8 n, n × PARAM_ENTRY` | RETRY | – | FW-CFG-002, PO-FW-5 |
| `0x11` | GET_PARAM | `u16 id` (2) | PARAM_ENTRY (7) | RETRY | – | FW-CFG-002 |
| `0x12` | SET_PARAM | PARAM_ENTRY (7) | PARAM_ENTRY as stored (7) | RETRY | – | FW-CFG-003, PO-FW-6 |
| `0x13` | SAVE_PARAMS | – (0) | – | VERIFY | – | FW-NVM-001 |
| `0x14` | LOAD_PARAMS | – (0) | – | VERIFY | – | FW-NVM-001 |
| `0x15` | DEFAULT_PARAMS | – (0) | – | VERIFY | – | FW-NVM-001 |
| `0x20` | STREAM_START | – (0) | – | RETRY | – | FW-STR-001, PO-FW-8 |
| `0x21` | STREAM_STOP | – (0) | – | RETRY | – | FW-STR-001 |
| `0x22` | SET_VALID | `u8 valid` 0/1 (1) | `u32 t_us` from which it applies | RETRY (newest value) | – | FW-CMD-002, PO-FW-7 |
| `0x30` | ENABLE | – (0) | `u16 settle_ms` (ms until ENABLED) | VERIFY | – | FW-MOT-008 |
| `0x31` | DISABLE | – (0) | – | VERIFY | – | FW-MOT-008 |
| `0x32` | HOME | `u8 flags` (home_flags, Appendix B): bit0 = operator confirmed load, bits 1–7 = 0 (1) | – | VERIFY | – | FW-HOM-001, SAF-FW-021, D-29b |
| `0x33` | MOVE_ABS | `i32 target_um, u32 v_um_s, u32 a_um_s2` (12) | – | VERIFY | – | FW-MOT-004 |
| `0x34` | JOG | `i32 v_um_s, u32 a_um_s2, i32 bound_um` (12) | – | JOG ≠ 0: VERIFY (refresh stream); JOG 0: RETRY | – | FW-MOT-005, SAF-FW-016 |
| `0x35` | MOVE_UNTIL_LOAD | `i32 bound_um, u32 v_um_s, u32 a_um_s2, i32 raw_stop, u8 cmp` (17) | – | VERIFY | – | FW-MOT-006 |
| `0x36` | STOP | `u8 mode` (stop_mode): 0 = immediate, 1 = controlled (1) | – | CONFIRM | priority / sniffed | FW-MOT-007, SW-STOP-001 |
| `0x37` | HALT | – (0) | – | CONFIRM | priority / sniffed | FW-MOT-007, SW-STOP-002 |
| `0x38` | HALT_CLEAR | – (0) | – | VERIFY (priority lane, D-34) | priority | FW-MOT-007, SAF-FW-023, D-34, D-36 |
| `0x39` | ESTOP_CLEAR | – (0) | – | VERIFY (priority lane, D-34) | priority | SAF-FW-006, D-34 |
| `0x3A` | FAULT_CLEAR | – (0) | `u16 cleared` (FAULT mask §7.6) | VERIFY (priority lane, D-34) | priority | FW-CMD-003, SAF-FW-011, D-34 |
| `0x3B` | PAUSE | – (0) | – | CONFIRM | priority / sniffed | SAF-FW-023, D-14, D-26, D-29a |
| `0x3C` | RESUME | – (0) | – | VERIFY (never auto-retried, §9.3) | – | SAF-FW-023, D-31 |
| `0x3D` | DIAG_MEAS | `u8 op, u8 sel, u16 a, u32 b` (meas_op, Appendix C) (8) | `u32 w[16]` (64 B, Appendix C) | per op (meas_op `retry`): RETRY for read ops, VERIFY otherwise | – | CR-02, D-40c, SYS-009, NFR-007 |

<!-- END GENERATED protocol:commands -->

There is **no relative-move command** (SAF-FW-020, IF-009): the SW converts ±0.1/1/10 mm and distance
entries into absolute targets.

Asynchronous (FW → PC): `0xC0` DATA (§7.3, IF-006), `0xC1` EVENT (§7.4, FW-STR-006) (table
`async_type`, Appendix B).

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

<!-- BEGIN GENERATED protocol:status_code (gen_protocol.py) -->

Generated from `protocol.yaml` table `status_code` (C `ST_*`, Python `Status`).

| Code | Name | Meaning | `detail` |
|---|---|---|---|
| 0 | `OK` | accepted / executed | (none) |
| 1 | `E_UNKNOWN_CMD` | TYPE in 0x01..0x3F not defined | the TYPE |
| 2 | `E_LENGTH` | LEN ≠ the command's fixed LEN | the expected LEN |
| 3 | `E_PARAM_ID` | unknown parameter id | the requested id |
| 4 | `E_TYPE` | PARAM_ENTRY type byte ≠ the parameter's type | the parameter id |
| 5 | `E_RANGE` | argument out of range (never clamped) | SET_PARAM: parameter id; other commands: byte offset of the offending field in the request payload |
| 6 | `E_CONFIG` | SET_PARAM violates a hard rule (§11.4) | id of the other parameter of the rule |
| 7 | `E_BUSY` | not possible now, retry later | busy_detail (Appendix B): 1 = MOTION (moving, homing or stopping), 2 = ENABLING (ENA settle running) |
| 8 | `E_STATE` | motion / enable / RESUME refused in the current state | BLOCK mask (§4.3): all blocking conditions evaluated for that command |
| 9 | `E_CAUSE_ACTIVE` | clear refused, cause still present | ESTOP_CLEAR: 0xFFFF = sense input open (latched or not), else ms still missing until io.estop_release_ms; HALT_CLEAR: never refused since v0.5 (D-36); FAULT_CLEAR: FAULT mask (§7.6) of the latched faults whose cause is still present |
| 10 | `E_CONFIRM` | HOME with load above home.max_load_raw without the confirmed flag | 0 |
| 11 | `E_NVM` | NVM failure | nvm_detail (Appendix B): 1 = no valid record (LOAD), 2 = erase/program error, 3 = verify error |
| 12 | `E_INTERNAL` | implementation error or command not in this build; nothing executed | internal_detail (Appendix B): 1 = NOT_IN_BUILD, 2 = INVARIANT; other values implementation-defined |

<!-- END GENERATED protocol:status_code -->

### 4.3 BLOCK mask (detail of `E_STATE`, SAF-FW-020)

Bit set when the condition holds for the refused command:

<!-- BEGIN GENERATED protocol:block (gen_protocol.py) -->

Generated from `protocol.yaml` table `block` (C `BLOCK_*`, Python `Block`).

| Bit | Name | Meaning |
|---|---|---|
| 0 | `ESTOP` | ESTOP latched or E-stop sense input open (also evaluated by RESUME) |
| 1 | `HALT` | HALT latched (PC: HALT command / Pause-Break key) (also evaluated by RESUME) |
| 2 | `FAULT` | any FAULT latched (§7.6) (also evaluated by RESUME) |
| 3 | `NOT_ENABLED` | motion state NOT_ENABLED (ENABLE never done, or DISABLE / E-stop / idle disable / driver power loss since) |
| 4 | `NOT_HOMED` | MOVE_ABS, MOVE_UNTIL_LOAD or JOG with a bound while not homed |
| 5 | `LIMIT` | motion toward an active or latched limit switch (direction = sign(target − x) or sign(v)); only motion away is accepted while latched (D-33h) |
| 6 | `AFE_STALE` | no HX711 sample for afe.timeout_ms |
| 7 | `AFE_SATURATED` | last HX711 sample at a rail |
| 8 | `DRV_UNPOWERED` | only with the optional power sense (drv.pwr_sense_enable, default 0 since CR-03 / D-41): the DRV_POWER input reads 'off' (D-28, D-29c) |
| 9 | `DRIVER_ALARM` | ALM start-block (SAF-FW-026, D-28): ALM active and driver power present (sense disabled → assumed present); new motion starts only (MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0 while not jogging) |
| 10 | `PAUSED` | PAUSED latched (D-30): MOVE_ABS, MOVE_UNTIL_LOAD, HOME and JOG ≠ 0 (incl. refreshes of a running jog) refused; cleared by RESUME (clears only PAUSED) or HALT_CLEAR (clears HALT and PAUSED) (D-31) |
| 11 | `MEAS_STATE` | DIAG_MEAS op not allowed in the current motion state: HANG needs a running motion, STATIC_LEVEL needs NOT_ENABLED (Appendix C, D-40c) |
| 12–15 | — | reserved (0) |

<!-- END GENERATED protocol:block -->

ENABLE evaluates only bits 0 and 8. HOME evaluates all bits except 4 and 5 (the homing sequence handles the
switches, FW-HOM-001). JOG 0 is never refused by state. Bit 10 PAUSED (D-30) refuses MOVE_ABS,
MOVE_UNTIL_LOAD, HOME and every JOG ≠ 0 — including a refresh of a running jog and a JOG arriving while
STOPPING (E_STATE is checked before E_BUSY, §4.4). **RESUME** evaluates only bits 0 ESTOP, 1 HALT and 2
FAULT (D-31); its detail carries just those bits. Bit 11 MEAS_STATE is used only by DIAG_MEAS (HANG needs a
running motion, STATIC_LEVEL needs NOT_ENABLED; Appendix C).

**ALM start-block (bit 9 DRIVER_ALARM; SAF-FW-026, D-28, D-16)** — applies **only to new motion starts**:
MOVE_ABS, MOVE_UNTIL_LOAD, HOME, and JOG ≠ 0 while the motion state is not JOG. It does **not** apply to a
JOG ≠ 0 received while jogging (speed / acceleration / bound refresh and dead-man refresh of the running jog),
nor to JOG 0, STOP, HALT, PAUSE, ENABLE, DISABLE or the clears. A running move is never stopped or altered by
ALM (D-16); ALM is reported (status ALM, EVENT ALM_CHANGED). With driver power absent the refusal reason is
DRV_UNPOWERED, not DRIVER_ALARM (A-20).

### 4.4 Check order (defines which NACK wins; FW-CMD-001)
1. **TYPE** defined → else `E_UNKNOWN_CMD`.
2. **LEN** → `E_LENGTH`.
2a. **Not in this build** → `E_INTERNAL` NOT_IN_BUILD (detail 1), nothing executed: DIAG_MEAS without
   FEAT_HW_MEAS (release and twin builds, D-40c); commands of a later milestone whose feature bit is 0.
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
`page_count = ceil(PARAM_COUNT / 20)` (dict_version 4: 47 parameters → 3 pages, 152/152/61 B frames);
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
parameters (`nvm: false`) keep their RAM values. No valid record → `E_NVM` 1, RAM unchanged, **no EVENT**; a
record that violates a hard rule → `E_NVM` 1, RAM unchanged, **no EVENT** (nothing was loaded or defaulted;
OBS-M1-03). On success: EVENT PARAMS_LOADED (arg = values replaced by defaults) or, for a record with another
dictionary hash, PARAMS_DEFAULTED (arg 3).

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
and the next DATA frame carries VALID = 0. **v0.7.4 (D-47 a):** the link-watchdog timeout clears VALID in **every**
motion state (also idle / not enabled, §9.1), with the same EVENT VALID_CLEARED (arg LINK_WDG 13).

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
  (incl. STOPPING) → `E_BUSY` 1 — **except JOG while jogging** (speed/bound change; not a new motion start,
  so the ALM start-block does not apply to it, §4.3). There is no duplicate
  acknowledgement: motion commands are never auto-retried (§9.3).
- While **PAUSED**, MOVE_ABS, MOVE_UNTIL_LOAD, HOME and JOG ≠ 0 (incl. refreshes) are refused with `E_STATE`
  BLOCK PAUSED (D-30). Motion commands **never clear** PAUSED; only HALT_CLEAR does (§5.5).
- Every motion ends with exactly **one MOVE_DONE** EVENT (reason §8.3, `value` = final position µm,
  `value2` = final step count). A motion ended by a stop source additionally produces EVENT STOPPED (cause)
  before MOVE_DONE (reason STOPPED).

**MOVE_ABS** (`target_um`, `v_um_s`, `a_um_s2`) — requires HOMED. Trapezoid/triangle profile (FW-MOT-003) to
the absolute target; MOVE_DONE reason TARGET. A target equal to the current position completes at once
(MOVE_DONE TARGET, no pulse).

**Un-homed travel window (normative, D-43b)** — when the axis becomes un-homed (boot, power-up, E-stop, DISABLE,
idle disable, driver-power loss, STEP_FAULT, homing failure — every event that clears HOMED) the FW latches the
current position as the **un-homed origin**. Un-homed JOG and HOME motion stays within origin ± `home.max_travel_um`:
a held jog that reaches the bound stops there (planned stop, MOVE_DONE SOFT_LIMIT), and a further JOG ≠ 0 toward a
reached bound is refused with `E_RANGE` 0 until the axis is homed (a JOG away from it is accepted; JOG 0 is never
refused). A HOME fast seek that reaches origin − `home.max_travel_um` without a START edge ends in HOME_NOT_FOUND.
The origin is not re-latched by a jog, a stop or a reset of the jog; HOMED ends the window (soft limits apply).
Check vectors: state key `unhomed_origin_um` (state_schema 3).

**JOG** (`v_um_s` signed, `a_um_s2`, `bound_um`) — `v ≠ 0`: move in the sign direction at |v| (offset 0
checked against `v_limit`), stopping with a planned deceleration at the end point: `bound_um` if given, else
the soft limit in that direction (homed) or the **un-homed travel window** bound (un-homed, D-43b, below).
`bound_um = 0x80000000` (`JOG_NO_BOUND`) = no bound; any other value MUST lie inside the soft limits and
strictly ahead of the axis in the jog direction (else `E_RANGE` 8) and requires HOMED (else `E_STATE`
NOT_HOMED) (SW_design F-B-15). A JOG while jogging changes speed, acceleration and bound on the fly (a
reversal decelerates to zero first, DIR setup respected) and refreshes the **dead-man**: without a JOG for
`motion.jog_timeout_ms` the FW performs a controlled stop (STOPPED cause JOG_DEADMAN; VALID unchanged)
(SAF-FW-016). End point reached → MOVE_DONE BOUND or SOFT_LIMIT. **JOG 0** = controlled stop of a running jog
(`motion.a_stop_um_s2`, MOVE_DONE JOG_ZERO); while not jogging it is an OK no-op; it is never refused by state.

**MOVE_UNTIL_LOAD** (`bound_um`, `v_um_s`, `a_um_s2`, `raw_stop`, `cmp`) — requires HOMED. Direction =
sign(`bound_um` − x); `bound_um` equal to the current position (no direction) → `E_RANGE` 0, like a bound
outside the soft limits (F-B-28; the SW refuses it locally anyway); `raw_stop` ∈ [−8 388 608, 8 388 607] (offset 12); `cmp` (offset 16): **0 = stop when
raw ≥ raw_stop**, **1 = stop when raw ≤ raw_stop** (the SW chooses `cmp` from the expected sign of the raw
change, i.e. sign(K) and the motion direction; F-B-04). Before the first pulse the last sample is compared:
already beyond → MOVE_DONE LOAD_THRESHOLD without motion (so a repeated command never moves further).
During the move **every** HX711 sample is compared; the first sample beyond `raw_stop` causes an immediate
stop with the load-path timing of SAF-FW-002 → MOVE_DONE LOAD_THRESHOLD. Reaching `bound_um` → planned stop
exactly at the bound → MOVE_DONE BOUND. Fallback frames are not samples (AFE stale while moving =
AFE_FAULT). Equality counts as beyond (`cmp` 0: raw ≥ `raw_stop`, `cmp` 1: raw ≤ `raw_stop`); no sample yet =
not beyond. **Execution rules (v0.7.2, OI-FW-43, A's FW_design v0.7 §5.4.4):**
- (a) **Load limit first**: a sample that trips the FW load limit (§5.5) is the load limit's stop — FAULT_SET
  LOAD_LIMIT, STOPPED (LOAD_LIMIT), MOVE_DONE STOPPED — never LOAD_THRESHOLD, even if it is also beyond `raw_stop`.
- (b) **Controlled stop in progress** (PAUSE, STOP mode 1, link watchdog): the compare stays armed; a sample
  beyond `raw_stop` cuts the deceleration short with a CLEAN halt; the MOVE_DONE reason stays STOPPED (the first
  cause is kept, no second STOPPED). A sample after the step output has stopped decides nothing.
- (c) **Bound within one step**: a `bound_um` ≠ the position that rounds to the current step completes at once
  without a pulse: MOVE_DONE LOAD_THRESHOLD if the last sample is beyond `raw_stop`, else MOVE_DONE BOUND.
- (d) **LOAD_THRESHOLD is not a stop source** (§6.2): no STOPPED, VALID unchanged, HOMED kept, POS_UNCERTAIN
  not set (the halt is CLEAN).
- (e) *Informative*: a MOVE_UNTIL_LOAD parked by the sniffed-stop hold (a STOP / HALT / PAUSE byte sequence seen
  by the stop sniffer before the command was dispatched, §2.4) is discarded with STOPPED like a MOVE_ABS.

**HOME** (`flags`) — bits 1–7 ≠ 0 → `E_RANGE` 0. Load pre-check (SAF-FW-021): `abs(raw − safety.zero_raw) >
home.max_load_raw` and bit0 = 0 → `E_CONFIRM`. **Homing uses only the START switch** (release 1, D-29b,
D-08, D-18; there is no home-switch parameter). Sequence FW-HOM-001: release (move +x off START if it is
active) → fast seek toward START (−x) at `home.v_fast_um_s` → immediate stop at the edge → back-off
`home.backoff_um` (+x) → slow approach toward START at `home.v_slow_um_s` with edge capture in the EXTI ISR →
set zero so that the captured START edge lies at x = −`home.offset_um` → move to 0; homing speeds are limited
to the step-rate cap (`home_phase` §7.2). If the axis was HOMED before, the captured edge is compared with its
expected position (−`home.offset_um`); a deviation > `home.drift_tol_um` latches fault HOME_DRIFT (value =
deviation µm) but homing completes (R5 §8). Success → HOMED, POS_UNCERTAIN cleared, EVENT HOMED (value =
deviation µm, 0 if not homed before), MOVE_DONE TARGET at 0. Failures (FW-HOM-002): no START edge within
`home.max_travel_um` (FAST_SEEK; un-homed: from the un-homed origin, D-43b) or within `home.backoff_um` + `HOME_SLOW_EXTRA_UM` (10 mm, SLOW_APPROACH) →
fault HOME_NOT_FOUND; the END switch reached, or START still active after `HOME_RELEASE_MAX_UM` (10 mm) of
travel in RELEASE or BACKOFF (stuck switch or inverted DIR) → fault HOME_WIRING (OI-FW-23; FW constants in
`protocol.yaml`, C `PROTO_HOME_*`); any other stop during homing → HOME_FAILED reason ABORTED (not a fault latch; the stopping cause has its own latch, if any). Every
failure: immediate stop, HOMED cleared, EVENT HOME_FAILED, MOVE_DONE STOPPED. Expected START edges during HOME
do not set the LIMIT_START latch.

**ENABLE** — BLOCK bits ESTOP or DRV_UNPOWERED → `E_STATE`. Drives ENA to the enabled level, waits
`motion.ena_settle_ms` (no PUL/DIR edge) and then sets ENABLED (EVENT DRIVER_ENABLED); the response body is
the remaining settle time in ms (0 if already enabled = no-op). (FW-MOT-008, D-28: 500 ms.)

**DISABLE** — moving → `E_BUSY` 1. Drives ENA to the disabled level, clears ENABLED and HOMED, EVENT
DRIVER_DISABLED (arg 1). The SW asks "specimen unloaded?" first (SAF-SW-004).

### 5.5 Stops, pause and clears (FW-MOT-007, SAF-FW-001…006, SAF-FW-023, D-14, D-26, D-36)
STOP, HALT, HALT_CLEAR, PAUSE, ESTOP_CLEAR and FAULT_CLEAR are accepted in **every** state (no `E_STATE` /
`E_BUSY`) and are idempotent. RESUME is idempotent but refused (`E_STATE`) while ESTOP, HALT or a FAULT is
latched. **No clear and no RESUME ever starts motion.**

**STOP** (`mode`) — `mode` > 1 → `E_RANGE` 0. 0 = immediate stop (no further PUL edge ≤ 2 ms after the last
command byte, SAF-FW-002), 1 = controlled stop (`motion.a_stop_um_s2`). Not latched; the running motion
target and ramp state are discarded (SAF-FW-001); VALID cleared (also when idle). EVENT STOPPED (cause
PC_STOP / PC_STOP_CONTROLLED) if a motion was running.

**HALT** — immediate stop + **HALT latch** (`halt_src` = PC: the HALT command, sent for GUI HALT and the
Pause/Break key) + VALID cleared; EVENT HALT_SET (arg = 1 PC) on the first HALT only. Since v0.5 there is no
physical holding STOP/BREAK button and no HALT source BUTTON (D-36, CR-01; SAF-FW-022 withdrawn): the single red
mushroom button is the E-stop (MCU sense + hardwired ENA cut, §6.2 E-stop row, D-41/D-42). Motion refused (`E_STATE` HALT) until
HALT_CLEAR.

**HALT_CLEAR** — never refused (no cause input since v0.5, D-36): clears HALT if latched (EVENT
HALT_CLEARED). An accepted HALT_CLEAR also clears **PAUSED** if set — whether or not HALT was latched
(EVENT PAUSE_CLEARED arg 2 = HALT_CLEAR; D-31: a HALT terminates the sequence, so the pause ends with it). No
motion restarts (SW-STOP-003). Nothing latched → OK, no other effect.

**RESUME** (D-31, F-B-30) — clears **only** PAUSED (EVENT PAUSE_CLEARED arg 3 = RESUME, `pause_src` → NONE).
Refused with `E_STATE` while ESTOP (latched or sense input open), HALT or any FAULT is latched (detail = those
BLOCK bits, §4.3); the NACK has no effect, so a HALT (Pause/Break key) latched just before the RESUME frame stays
latched together with PAUSED. No other condition is evaluated (not DRV_UNPOWERED, ALM, LIMIT, AFE, NOT_ENABLED,
NOT_HOMED, motion state; never `E_BUSY`): the motion command that follows is gated as usual. Not PAUSED → OK,
no-op, no event. RESUME refreshes the link watchdog like every command frame; it is not handled by the stop
sniffer (§2). Retry class VERIFY (§9.3).

**PAUSE** — the PC equivalent of the physical PAUSE button, with identical FW behaviour (D-29a, F-B-03, GF-01):
while moving → controlled stop (STOPPED cause PC_PAUSE, MOVE_DONE STOPPED); in every state PAUSED is set and
VALID cleared.

**PAUSED latch** (normative, D-30 amending D-29a) —
- *Set* by an accepted PAUSE (source PC) or a press of the physical PAUSE button while not PAUSED (source
  BUTTON; STOPPED cause PAUSE_BUTTON if moving). On the 0 → 1 transition the FW records the source in STATUS
  `pause_src` and sends EVENT PAUSED (arg = source, Appendix B `source`). DATA/STATUS `status.PAUSED` = 1.
- *Already PAUSED*: PAUSE → OK, no event, source unchanged; a button press → EVENT RESUME_REQUEST only
  (the FW does not resume and does not change the source).
- *Blocks motion*: while PAUSED, MOVE_ABS, MOVE_UNTIL_LOAD, HOME and JOG ≠ 0 — also a refresh of a jog that
  is still running or stopping — are refused with `E_STATE`, BLOCK bit PAUSED (§4.3). A motion command that
  was already in flight when PAUSE was pressed therefore cannot restart motion. Not blocked: JOG 0, STOP,
  HALT, PAUSE, ENABLE, DISABLE, the clears, parameter, NVM, stream and query commands.
- *Cleared* **only** by an accepted RESUME (EVENT PAUSE_CLEARED arg 3) or an accepted HALT_CLEAR (arg 2);
  `pause_src` → NONE. Nothing else clears it: no motion command, JOG 0, STOP, HALT, PAUSE, ENABLE, DISABLE,
  ESTOP_CLEAR, FAULT_CLEAR, link restoration, E-stop or driver power loss, and not a NACKed RESUME /
  HALT_CLEAR (§4.1). Boot: PAUSED = 0.
- *Resume* (GUI Resume, or the PC's reaction to RESUME_REQUEST) is a PC action: **RESUME, then re-issue the
  interrupted step's absolute target** (approach + trim, SW-STOP-004; D-31). A RESUME refused with `E_STATE`
  is an expected outcome (no retry; the sequence stays PAUSED or is terminated by the HALT). The FW never
  resumes by itself. `check_vectors.json` carries `expect.paused_after` for every vector with PAUSED set and
  for PAUSE / RESUME.

**ESTOP_CLEAR** — if ESTOP is latched: refused with `E_CAUSE_ACTIVE` (0xFFFF = input open, else the missing
ms) until the sense input has been closed continuously for `io.estop_release_ms`; otherwise clears ESTOP
(EVENT ESTOP_CLEARED). Afterwards the driver stays disabled and the axis not homed: ENABLE, then HOME, are
required (SAF-FW-006). Nothing latched → OK.

**FAULT_CLEAR** — clears all latched faults whose cause is gone; if any latched fault (other than
LOAD_LIMIT) still has its cause present → `E_CAUSE_ACTIVE` (detail = FAULT mask of those) and **nothing** is
cleared. LOAD_LIMIT is always clearable so the operator can unload (regrow window below). Causes:
AFE_FAULT — AFE stale or last sample saturated; LIMIT_WIRING — both limit inputs active **at the same time**:
FAULT_CLEAR is accepted as soon as they are no longer both active; an input that is still active keeps acting
as a normal limit latch (motion toward it refused, BLOCK LIMIT; D-40a, SAF-FW-014); K1_WELDED — E-stop
sense open while driver power present (any duration); STEP_FAULT, HOME_NOT_FOUND, HOME_WIRING, HOME_DRIFT — none (clearable
at once). OK body = mask of the cleared faults; EVENT FAULT_CLEARED (arg = that mask).

**FW load limit and regrow window (normative; SAF-FW-008…011, D-12, D-40d; oracle `ref_loadlim.py`, vectors
`loadlim_vectors.json`)** —
- Every HX711 sample is a **violation** when raw > `safety.load_raw_max`, raw < `safety.load_raw_min` or the sample
  is at a rail (SAF-FW-009). `safety.load_trip_samples` consecutive violations trip (immediate stop, LOAD_LIMIT);
  a sample inside the thresholds resets the count. Threshold changes act from the next sample and keep the count.
- Only an accepted FAULT_CLEAR that clears a **latched LOAD_LIMIT** (OK body bit 0) takes the **reference** = the
  last sample (raw; 0 if there is none yet). A FAULT_CLEAR of other faults, or with LOAD_LIMIT not latched, leaves
  the load-limit state unchanged (OI-B-M2-03). If the reference sample is a violation the **regrow window** opens: a violating sample re-trips **immediately** (no trip-sample count) only if it lies more
  than `safety.load_regrow_raw` beyond the reference on its side (raw > max and raw > ref + regrow, or raw < min and
  raw < ref − regrow); any other violating sample is ignored (unloading allowed) and does not count.
- The window **ends** with the first sample inside the thresholds (the normal rule applies from the next sample) or
  with a re-trip (LOAD_LIMIT latched again; its FAULT_CLEAR takes the next reference). A threshold change does not
  end it. A clear with the last sample
  inside the thresholds opens no window.

### 5.6 Diagnostics: DIAG_MEAS (D-40c, CR-02; FW_test_plan §6)
**DIAG_MEAS** (0x3D, LEN 8: `u8 op, u8 sel, u16 a, u32 b`; OK body `u32 w[16]`, 64 B) exists only in the
**measurement build** (`HW_MEAS`, INFO feature bit 9 **FEAT_HW_MEAS**). The release image and the twin (without its
HW_MEAS model) answer `E_INTERNAL` NOT_IN_BUILD after the LEN check, nothing executed (§4.4 step 2a). In a HW_MEAS
build: op ≤ 9 and the per-op `sel` / `a` / `b` rules of Appendix C (unused fields must be 0) → else `E_RANGE` with
the field offset (0 / 1 / 2 / 4); HANG while not moving or STATIC_LEVEL while not NOT_ENABLED → `E_STATE` MEAS_STATE;
read ops are accepted in every state. DIAG_MEAS starts no motion and changes no parameter, latch or safety reaction;
it is not sniffed, goes on the normal lane, refreshes the link watchdog like every command, and is answered ≤ 10 ms.
Retry class per op (Appendix C): RETRY for read ops, VERIFY for ARM / STIM / HANG / STATIC / resets. The core forwards
the validated request to seam v1.3 `hal_meas_cmd()` (tools/README); `HW_MEAS` code lives only in `hal/f446/meas*.c`
and the safety handlers / core stay byte-identical to the release image (TC-SYS-009-02). Word layouts: **Appendix C**.

---

## 6. FW state model (SRS §3.2)

### 6.1 Motion state (`motion_state`, STATUS)

<!-- BEGIN GENERATED protocol:motion_state (gen_protocol.py) -->

Generated from `protocol.yaml` table `motion_state` (C `MS_*`, Python `MotionState`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `NOT_ENABLED` | after boot, DISABLE, E-stop, idle disable or driver power loss; no pulse possible (DATA ENABLED = 0) |
| 1 | `ENABLING` | ENA asserted, motion.ena_settle_ms running (ENABLED = 0) |
| 2 | `IDLE` | enabled, standing (holding) (ENABLED = 1, MOVING = 0) |
| 3 | `MOVE_ABS` | executing MOVE_ABS (MOVING = 1) |
| 4 | `JOG` | executing JOG (MOVING = 1) |
| 5 | `MOVE_UNTIL_LOAD` | executing MOVE_UNTIL_LOAD (MOVING = 1) |
| 6 | `HOMING` | executing HOME, see home_phase (MOVING = 1) |
| 7 | `STOPPING` | controlled deceleration in progress (MOVING = 1) |

<!-- END GENERATED protocol:motion_state -->

HOMED is orthogonal (flag). Transitions: ENABLE: NOT_ENABLED → ENABLING → IDLE; motion command: IDLE → 3…6 →
IDLE (MOVE_DONE); controlled stop: 3…6 → STOPPING → IDLE (or 3…6 → IDLE directly when executed as a clean
halt, §6.5); immediate stop: 3…7 → IDLE; DISABLE / E-stop / idle disable / driver power loss: any →
NOT_ENABLED.

### 6.2 Stop sources, latches and clear conditions (SRS §3.2, SAF-FW-001…023)

| Trigger | Stop | ENA | Latch → clear | HOMED | VALID | EVENTs |
|---|---|---|---|---|---|---|
| E-stop sense opens (MCU / FW only, D-41; the E-stop NO contact also forces ENA disabled in hardware, D-42) | immediate (≤ 100 µs) | disabled ≤ 1 ms | ESTOP → ESTOP_CLEAR (input closed ≥ `io.estop_release_ms`), then ENABLE + HOME | cleared | cleared | ESTOP_SET, STOPPED (ESTOP), DRIVER_DISABLED (3) |
| PC STOP | immediate / controlled | kept | none | kept | cleared | STOPPED |
| PC HALT | immediate | kept | HALT (PC) → HALT_CLEAR | kept | cleared | HALT_SET, STOPPED |
| physical PAUSE button / PC PAUSE | controlled (if moving) | kept | PAUSED (`pause_src` BUTTON / PC): new motion refused (BLOCK PAUSED); cleared **only** by RESUME or HALT_CLEAR (D-30, D-31, §5.5); button press while PAUSED → RESUME_REQUEST only | kept | cleared | PAUSE_BUTTON (button only), PAUSED (arg = source), STOPPED (PAUSE_BUTTON / PC_PAUSE) |
| limit switch while moving | immediate (≤ 200 µs) | kept | LIMIT_x → auto-clear when the input has been released continuously for `io.release_ms`; while latched only motion **away** from the switch is accepted (BLOCK LIMIT toward it) (D-33h) | kept | cleared | LIMIT_SET, STOPPED, LIMIT_CLEARED |
| both limit inputs active | immediate | kept | fault LIMIT_WIRING → inputs no longer both active + FAULT_CLEAR; a still-active input then acts as a LIMIT latch (D-40a) | kept | cleared | FAULT_SET, STOPPED |
| MOVE_UNTIL_LOAD threshold sample (v0.7.2, OI-FW-43; not a stop source) | immediate CLEAN (≤ 200 µs after DRDY); during a controlled stop it only cuts the ramp (reason stays STOPPED) | kept | none | kept | **kept** | MOVE_DONE LOAD_THRESHOLD only (no STOPPED) |
| FW load limit / rail sample | immediate (≤ 200 µs after DRDY) | kept | fault LOAD_LIMIT → FAULT_CLEAR (always; regrow window until a sample is inside the thresholds or a re-trip, §5.5, D-40d) | kept | cleared | FAULT_SET, STOPPED |
| AFE stale while moving | immediate | kept | fault AFE_FAULT → fresh samples + FAULT_CLEAR | kept | cleared | AFE_STALE, FAULT_SET, STOPPED |
| link watchdog (moving) | controlled | kept | LINK_WDG status → next valid frame | kept | cleared | LINK_WDG, STOPPED, LINK_RESTORED |
| link watchdog (not moving, v0.7.4, D-47 a; not a stop) | none | kept | none (no LINK_WDG status) | kept | **cleared** | VALID_CLEARED (LINK_WDG) only if VALID was 1 |
| jog dead-man | controlled | kept | none | kept | **unchanged** | STOPPED (JOG_DEADMAN) |
| step overrun / count fault | immediate | kept | fault STEP_FAULT → FAULT_CLEAR | **cleared** | cleared | FAULT_SET, STOPPED |
| homing failure | immediate | kept | fault HOME_NOT_FOUND / HOME_WIRING → FAULT_CLEAR (ABORTED: no latch) | cleared | cleared | HOME_FAILED, STOPPED |
| home drift at re-homing | none (homing completes) | kept | fault HOME_DRIFT → FAULT_CLEAR | set (new zero) | — | FAULT_SET, HOMED |
| **only with `drv.pwr_sense_enable` and `drv.k1_check_enable`** (both default 0; CR-03, SRS OI-18; no contactor in release 1): E-stop sense open while driver power stays present > `drv.k1_weld_ms` (default 200 ms) | (already stopped by the E-stop row) | disabled | fault K1_WELDED, latched ≤ `drv.k1_weld_ms` + 25 ms after the E-stop sense edge (D-33f) → cause gone (E-stop closed or power off) + FAULT_CLEAR (D-29c, R5 §1.5) | cleared | cleared | FAULT_SET (arg 6, value = ms) |
| **only with the optional power sense**: driver power lost (PSU loss / 48 V presence input off) | pulses ended ≤ 25 ms after the input change (SAF-FW-024) | disabled level | none: motion and ENABLE refused (`E_STATE` DRV_UNPOWERED; motion also NOT_ENABLED) while off; after power returns ENABLE (+ settle) and HOME are required (D-29c, D-11) | **cleared** (position lost) | cleared | DRIVER_POWER (0) always; STOPPED (DRV_POWER_LOST) if a motion was still running; DRIVER_DISABLED (4) if the state was not already NOT_ENABLED |
| idle ≥ `safety.idle_disable_s` and unloaded (never while the AFE is stale: unloaded state unknown, D-33g) | — | disabled | none → ENABLE | cleared | — | DRIVER_DISABLED (2) |
| PC DISABLE | refused while moving | disabled | — | cleared | — | DRIVER_DISABLED (1) |
| ALM active | **no stop** (D-16); ALM start-block: new motion starts refused while driver power present, running-jog refreshes / STOP / HALT / PAUSE / ENABLE not blocked (§4.3, SAF-FW-026, D-28) | kept | — | — | — | ALM_CHANGED |
| MCU reset / IWDG / power-up | PUL idle from reset | left at "no current" (driver enabled, holding; disabled if E-stop open) | boot: NOT_ENABLED | cleared | 0 | BOOT |

Every immediate stop that may have truncated a pulse in flight sets `POS_UNCERTAIN` (±1 step, HOMED kept;
cleared by the next HOME) (SAF-FW-004). **v0.7.2 (MC2-6):** the E-stop stop (TRUNCATE) sets it whenever the step
output was running at the E-stop edge — the core does not know the pulse phase at the edge, so "may have"
means "was running" (A's FW; the SW simulator follows, SWC-M3-01); CLEAN stops (STOP 0, HALT, limit switch, load
limit, MOVE_UNTIL_LOAD threshold) never set it. A stopped move is never resumed by the FW.

**E-stop without contactor (CR-03, D-41, D-42)** — release 1 has no power-removal contactor: the red NC button
goes to the MCU E-stop sense (PA10) only, and its additional NO contact drives the HBS86H ENA opto to *disabled*
directly, independent of the MCU (D-42). The FW reaction is unchanged (E-stop row). The FW **must tolerate the
externally forced ENA state**: no fault and no latch from an ENA level that differs from the commanded one while
the E-stop is active; STATUS `io.ENA_DISABLED` reports the level the MCU drives. Not an IEC 60204-1 emergency stop
(residual risk accepted by the PO, D-41).

**Driver power (D-29c, D-28, R5 §1.5; optional since CR-03)** — evaluated only with `drv.pwr_sense_enable` = true
(default **false** since dict_version 5: no contactor to watch; the input remains as an optional 48 V presence
sense). With it false, power is assumed present, DRV_PWR reads 1 (feature bit permitting) and K1_WELDED is never
detected. The **K1_WELDED check** additionally requires `drv.k1_check_enable` = true (default **false**, reboot
required; SRS OI-18): only a power-removal contactor whose aux contact feeds DRV_POWER makes "power present while
the E-stop is open" a fault. With the check off, DRV_POWER present during an E-stop is **reported only** (status
DRV_PWR = 1, no EVENT, no fault); a plain 48 V presence sense would otherwise latch K1_WELDED on every E-stop. The
DRV_POWER input is debounced by the FW (FW_design). The text below applies with the sense enabled.
- E-stop opens first (normal case): the E-stop row applies (motion already stopped ≤ 100 µs, NOT_ENABLED,
  HOMED and VALID cleared); a power drop that follows (optional sense) produces EVENT DRIVER_POWER (0) — STOPPED and
  DRIVER_DISABLED are not repeated because no motion is running and the state is already NOT_ENABLED
  (OI-ICD-06). If the power is still reported present
  continuously for more than `drv.k1_weld_ms` after the sense input opened and `drv.k1_check_enable` is set, FAULT K1_WELDED is latched
  (power sense wired to a supply the E-stop does not cut: expected without a contactor, so keep the sense off); the E-stop sense path has already stopped the axis and dropped
  ENA.
- Driver power lost while the E-stop sense input is closed (PSU loss):
  within 25 ms of the input change (1 kHz sampling + 20 ms filter, SAF-FW-024; during an NVM erase/program —
  idle only — within the operation time + 25 ms, D-33f) pulse generation ends
  (STOPPED cause DRV_POWER_LOST if a motion was running; the active move is discarded), ENA goes to the
  disabled level, motion state NOT_ENABLED, **HOMED cleared** (an unpowered driver loses position, D-11),
  VALID cleared. No latch: the state follows the input. While it reads "off", ENABLE is refused (`E_STATE`
  DRV_UNPOWERED) and motion commands report NOT_ENABLED + DRV_UNPOWERED (+ NOT_HOMED where applicable). After
  power returns: ENABLE, whose settle `motion.ena_settle_ms` counts from the later of ENABLE and power return
  (FW-MOT-008), then HOME.

### 6.3 Command acceptance matrix (summary; `check_vectors.json` is normative)
"✓" = accepted; "B" = `E_BUSY`; "S(bit)" = `E_STATE` with that BLOCK bit; "C" = `E_CAUSE_ACTIVE`.

| Command | NOT_ENABLED | ENABLING | IDLE | moving (3–6) | STOPPING | ESTOP latched | HALT latched | FAULT latched | PAUSED | not homed |
|---|---|---|---|---|---|---|---|---|---|---|
| PING, GET_*, STREAM_*, SET_VALID | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| SET_PARAM | ✓ | ✓ | ✓ | `moving_ok` only, else B | as moving | ✓ | ✓ | ✓ | ✓ | ✓ |
| SAVE / LOAD / DEFAULT_PARAMS, REBOOT | ✓ | ✓ | ✓ | B | B | ✓ | ✓ | ✓ | ✓ | ✓ |
| ENABLE | ✓ | ✓ (no-op) | ✓ (no-op) | ✓ (no-op) | ✓ (no-op) | S(ESTOP) | ✓ | ✓ | ✓ | ✓ |
| DISABLE | ✓ | ✓ | ✓ | B | B | ✓ | ✓ | ✓ | ✓ | ✓ |
| MOVE_ABS, MOVE_UNTIL_LOAD | S(NOT_ENABLED) | B(2) | ✓ | B | B | S(ESTOP) | S(HALT) | S(FAULT) | S(PAUSED) | S(NOT_HOMED) |
| JOG ≠ 0 | S(NOT_ENABLED) | B(2) | ✓ | JOG: ✓, else B | B | S(ESTOP) | S(HALT) | S(FAULT) | S(PAUSED) (also refreshes) | ✓ (≤ v_unhomed, no bound) |
| HOME | S(NOT_ENABLED) | B(2) | ✓ (E_CONFIRM if loaded) | B | B | S(ESTOP) | S(HALT) | S(FAULT) | S(PAUSED) | ✓ |
| JOG 0, STOP, HALT, PAUSE | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| HALT_CLEAR | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (clears HALT) | ✓ | ✓ (clears PAUSED) | ✓ |
| ESTOP_CLEAR / FAULT_CLEAR | ✓ | ✓ | ✓ | ✓ | ✓ | C while cause active (ESTOP_CLEAR) | ✓ | C while cause active (FAULT_CLEAR) | ✓ | ✓ |
| RESUME | ✓ | ✓ | ✓ | ✓ (no-op) | ✓ | S(ESTOP) | S(HALT) | S(FAULT) | ✓ (clears PAUSED) | ✓ |

Additional refusals of motion commands: LIMIT toward an active/latched switch, AFE stale/saturated,
DRV_UNPOWERED, DRIVER_ALARM (§4.3). An S(…) cell lists the deciding bit; the NACK detail carries every
blocking bit.

### 6.4 Boot (SAF-FW-018, SAF-FW-007, D-13)
PUL idle from reset release; ENA left at the "no current" level (driver enabled, holding) unless the E-stop
input is open **or** (`drv.pwr_sense_enable` and DRV_POWER reads off) — then the disabled level, so the driver
comes up disabled when its supply returns (OI-FW-22, optional sense, consistent with §6.2); motion state NOT_ENABLED; HOMED = 0;
VALID = 0; PAUSED = 0; stream off; inputs active at boot (E-stop, limits) are latched/reported
from the first GET_STATUS and refuse motion accordingly; parameters per §11.3; EVENT BOOT queued (arg = reset
cause; value / value2 = faulting PC / CFSR of a HardFault recorded before the reset, else 0 / 0; OI-FW-21).

### 6.5 Controlled stop at very low speed (D-29d, D-30, SAF-FW-003, OI-FW-07)
A controlled stop (STOP mode 1, PAUSE, PAUSE button, link watchdog, jog dead-man, JOG 0) must start
decelerating within 2 ms. The FW **MAY** execute it as a **clean immediate halt** instead only when **both**
hold at the moment of the request (D-30):
1. the current step period is **longer than 2 ms** (step rate < 500 steps/s, e.g. < 0.625 mm/s at the
   default 800 steps/mm), and
2. the planned stop distance computed by the FW, v²/(2·a_stop) in steps (v in steps/s, a_stop =
   `motion.a_stop_um_s2 · motion.steps_per_mm / 1000` in steps/s²), is **≤ 1 step**.

Otherwise the FW decelerates as planned with `motion.a_stop_um_s2` (also at a step period > 2 ms; then the
deceleration takes effect at the latest with the next step-timer update, OI-ICD-07). Clean halt: no further
pulse is started, the pulse in flight is completed (no truncated pulse → `POS_UNCERTAIN` is **not** set), the
motion state goes directly to IDLE (no STOPPING). Everything else is that of the controlled stop: same stop
cause in STOPPED / VALID_CLEARED, MOVE_DONE STOPPED (JOG 0: JOG_ZERO), latches and VALID as in §6.2. With the
defaults (1 000 000 µm/s² × 800 steps/mm = 800 000 steps/s²) condition 2 holds at every speed that satisfies
condition 1 (≤ 0.16 step; condition 2 matters only for `a_stop_um_s2 · steps_per_mm / 1000` < 125 000).

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
| 40 | u32 | `feature_mask` | bit table `features` below; others 0. The SW greys out functions whose bit is 0. |

<!-- BEGIN GENERATED protocol:features (gen_protocol.py) -->

Generated from `protocol.yaml` table `features` (C `FEAT_*`, Python `Features`).

| Bit | Name | Meaning |
|---|---|---|
| 0 | `AFE` | real HX711 |
| 1 | `AFE_SYNTHETIC` | M1 placeholder samples |
| 2 | `MOTION` | step generation |
| 3 | `HOMING` | HOME command |
| 4 | `MOVE_UNTIL_LOAD` | MOVE_UNTIL_LOAD command |
| 5 | `NVM` | SAVE/LOAD_PARAMS |
| 6 | `TWIN` | host twin build |
| 7 | `BUTTONS` | PAUSE button input (the STOP/BREAK input is retired, D-36) |
| 8 | `DRV_SIGNALS` | ALM/PEND/DRV_POWER inputs |
| 9 | `HW_MEAS` | measurement build (HW_MEAS, CR-02 / D-40c): DIAG_MEAS 0x3D executes; 0 = release / twin -> E_INTERNAL NOT_IN_BUILD |
| 10–31 | — | reserved (0) |

<!-- END GENERATED protocol:features -->

### 7.2 STATUS (86 bytes) (FW-CMD-004, NFR-005, NFR-006, F-B-02, GF-01)

| Off | Type | Field | Notes |
|---|---|---|---|
| 0 | u32 | `uptime_ms` | since boot |
| 4 | u32 | `t_us` | device time now (PC-device time pairs) |
| 8 | u8 | `flags` | DATA flags (§7.6) as of now |
| 9 | u8 | `motion_state` | §6.1 |
| 10 | u16 | `status` | DATA status bits (§7.6) as of now |
| 12 | u16 | `faults` | latched FAULT mask (§7.6) |
| 14 | u16 | `io` | input/output levels (§7.6) |
| 16 | u8 | `home_phase` | Appendix B `home_phase` (0 NONE … 7 DONE) |
| 17 | u8 | `halt_src` | HALT source, Appendix B `source`: 0 NONE, 1 PC, 2 BUTTON |
| 18 | u8 | `reset_cause` | Appendix B `reset_cause` |
| 19 | u8 | `sys_flags` | bit table `sys_flags` below |
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
| 70 | u16 | `idle_disable_left_s` | seconds until idle disable; `0xFFFF` = not counting (loaded, AFE stale, moving, disabled or 0 = never) |
| 72 | u32 | `nvm_record_seq` | sequence number of the active NVM record (0 = none) |
| 76 | u32 | `nvm_save_uptime_ms` | `uptime_ms` at the last SAVE (0 = none since boot) |
| 80 | u32 | `v_limit_um_s` | current MOVE_ABS speed cap (§5.4) |
| 84 | u8 | `pause_src` | PAUSED source (v0.2, GF-01, D-29a), Appendix B `source`: 0 NONE (not PAUSED), 1 PC (PAUSE command), 2 BUTTON (physical PAUSE button) |
| 85 | u8 | `reserved` | 0 |

<!-- BEGIN GENERATED protocol:sys_flags (gen_protocol.py) -->

Generated from `protocol.yaml` table `sys_flags` (C `SYSF_*`, Python `SysFlags`).

| Bit | Name | Meaning |
|---|---|---|
| 0 | `CLK_FALLBACK` | running on HSI fallback clock (FW-PLT-002); timing ±1 % |
| 1 | `CFG_DIRTY` | RAM parameters differ from the NVM record (§11.2) |
| 2 | `STREAM_ON` | DATA stream on |
| 3 | `REBOOT_PENDING` | a reboot_required parameter was changed (effective after SAVE_PARAMS + REBOOT) |
| 4 | `NVM_DEFAULTED` | defaults after boot/LOAD rules 2/4/5, until the next SAVE or clean LOAD |
| 5–7 | — | reserved (0) |

<!-- END GENERATED protocol:sys_flags -->

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
Reserved and **retired** bits are sent as 0 and ignored by receivers; a retired bit position is never reused
(D-36: STOP_BTN in `status` bit 9 and `io` bit 3).

**Feature-dependent bits (D-37b, IF-C-M1-02):** a bit marked "Valid only with `FEAT_x`" (DATA/STATUS
`status` PAUSE_BTN, ALM, PEND, DRV_PWR; STATUS `io` PAUSE_BTN, ALM, PEND, DRV_PWR) is **sent as 0 while that
INFO feature bit is 0** and is then **invalid**: the SW shows it as UNKNOWN (not "inactive"), never as a
measured value. The simulator mirrors the FW feature mask (single source: `protocol.yaml` `feature:`, Python
`<ID>_FEATURE`).

**DATA `flags` (u8)** (also STATUS `flags`):

<!-- BEGIN GENERATED protocol:data_flags (gen_protocol.py) -->

Generated from `protocol.yaml` table `data_flags` (C `DF_*`, Python `DataFlags`).

| Bit | Name | Meaning |
|---|---|---|
| 0 | `VALID` | data validity (D-05): SET_VALID, cleared by every operational stop except the jog dead-man |
| 1 | `MOVING` | motion state MOVE_ABS, JOG, MOVE_UNTIL_LOAD, HOMING or STOPPING |
| 2 | `HOMED` | machine zero valid |
| 3 | `ENABLED` | driver enabled and settled (motion state ≥ IDLE) |
| 4 | `ESTOP` | ESTOP latched or E-stop sense input open |
| 5 | `HALT` | HALT latched (PC HALT command / Pause-Break key; STATUS halt_src = PC) |
| 6 | `FAULT` | any FAULT latched (STATUS faults) |
| 7 | `OVERRUN` | ≥ 1 DATA frame dropped or ≥ 1 conversion missed by the FW since the previous sent frame |

<!-- END GENERATED protocol:data_flags -->

**DATA `status` (u16)** (also STATUS `status`):

<!-- BEGIN GENERATED protocol:data_status (gen_protocol.py) -->

Generated from `protocol.yaml` table `data_status` (C `DS_*`, Python `DataStatus`).

| Bit | Name | Meaning | Valid only with |
|---|---|---|---|
| 0 | `PAUSED` | PAUSED latch (source: STATUS pause_src, EVENT PAUSED arg); blocks new motion (BLOCK PAUSED); cleared by RESUME (clears only PAUSED) or HALT_CLEAR (clears HALT and PAUSED) (§5.5, D-30, D-31) | – |
| 1 | `LIMIT_START` | START limit input active or LIMIT_START latched | – |
| 2 | `LIMIT_END` | END limit input active or LIMIT_END latched | – |
| 3 | `LOAD_LIMIT` | FAULT LOAD_LIMIT latched | – |
| 4 | `AFE_STALE` | no HX711 sample for afe.timeout_ms | – |
| 5 | `AFE_SATURATED` | this sample at a rail | – |
| 6 | `AFE_SETTLING` | sample within afe.settle_discard after a (re)configuration | – |
| 7 | `AFE_RATE_MISMATCH` | measured rate deviates more than afe.rate_tol_pct | – |
| 8 | `LINK_WDG` | link watchdog tripped, until the next valid command frame | – |
| 9 | ~~`STOP_BTN`~~ | **retired in v0.5**: reserved, sent as 0, never reused — was: physical STOP/BREAK button input active. D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (MCU sense, D-41) | – |
| 10 | `PAUSE_BTN` | physical PAUSE button input active | `FEAT_BUTTONS` |
| 11 | `ALM` | driver ALM active | `FEAT_DRV_SIGNALS` |
| 12 | `PEND` | driver PEND (in position) active | `FEAT_DRV_SIGNALS` |
| 13 | `POS_UNCERTAIN` | an immediate stop may have truncated a pulse (±1 step), cleared by the next HOME | – |
| 14 | `NO_AFE_DATA` | fallback frame (afe_raw = 0x80000000) | – |
| 15 | `DRV_PWR` | driver power present; evaluated only with the optional power sense (drv.pwr_sense_enable, default 0, CR-03 / D-41): reads 1 when it is off and FEAT_DRV_SIGNALS = 1 | `FEAT_DRV_SIGNALS` |

<!-- END GENERATED protocol:data_status -->

A frame belongs to a **steady-state window** (SW-REP-002) only if VALID = 1, MOVING = 0 and none of flags
bits 4–7 and status bits 0–8, 13, 14 is set.

**FAULT mask (u16)** (STATUS `faults`, FAULT_CLEAR body/detail, FAULT_SET arg = bit index):

<!-- BEGIN GENERATED protocol:faults (gen_protocol.py) -->

Generated from `protocol.yaml` table `faults` (C `FAULT_*`, Python `Faults`).

| Bit | Name | Meaning |
|---|---|---|
| 0 | `LOAD_LIMIT` | FW load limit or rail sample; always clearable (re-trip on regrow, SAF-FW-011) |
| 1 | `AFE_FAULT` | AFE stale while moving; cause: AFE stale or last sample saturated |
| 2 | `STEP_FAULT` | step overrun / count fault; HOMED cleared; no persistent cause |
| 3 | `LIMIT_WIRING` | both limit inputs active; cause: both still active |
| 4 | `HOME_NOT_FOUND` | no START edge within home.max_travel_um; no persistent cause |
| 5 | `HOME_WIRING` | END switch reached during homing; no persistent cause |
| 6 | `K1_WELDED` | Power-removal device did not open with the E-stop (only with the optional power sense and the K1 check enabled: drv.pwr_sense_enable and drv.k1_check_enable, both default 0; SRS OI-18): E-stop sense open while driver power stays present > drv.k1_weld_ms (D-29c); cause: E-stop open and power present |
| 7 | `HOME_DRIFT` | re-homing edge deviates > home.drift_tol_um; no persistent cause |
| 8–15 | — | reserved (0) |

<!-- END GENERATED protocol:faults -->

**IO mask (u16)** (STATUS `io`; "active" after polarity). A limit that is latched but no longer active = status LIMIT_x set and io LIMIT_x clear.

<!-- BEGIN GENERATED protocol:io (gen_protocol.py) -->

Generated from `protocol.yaml` table `io` (C `IO_*`, Python `IoBits`).

| Bit | Name | Meaning | Valid only with |
|---|---|---|---|
| 0 | `ESTOP_OPEN` | E-stop sense input open | – |
| 1 | `LIMIT_START` | START limit input active | – |
| 2 | `LIMIT_END` | END limit input active | – |
| 3 | ~~`STOP_BTN`~~ | **retired in v0.5**: reserved, sent as 0, never reused — was: STOP/BREAK button input active (PC7 is no longer an input). D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (MCU sense, D-41) | – |
| 4 | `PAUSE_BTN` | PAUSE button input active | `FEAT_BUTTONS` |
| 5 | `ALM` | driver ALM input active | `FEAT_DRV_SIGNALS` |
| 6 | `PEND` | driver PEND input active | `FEAT_DRV_SIGNALS` |
| 7 | `DRV_PWR` | raw driver-power sense input 'powered' (optional 48 V presence sense, CR-03) | `FEAT_DRV_SIGNALS` |
| 8 | `ENA_DISABLED` | ENA output at the disabled level | – |
| 9 | `RATE_80` | HX711 RATE output high | – |
| 10–15 | — | reserved (0) | – |

<!-- END GENERATED protocol:io -->

**sys_flags**: §7.2. **feature_mask**: §7.1. **BLOCK mask**: §4.3. All other enums: Appendix B.

---

## 8. EVENT codes (FW-STR-006)

### 8.1 Codes

<!-- BEGIN GENERATED protocol:event (gen_protocol.py) -->

Generated from `protocol.yaml` table `event` (C `EV_*`, Python `Event`).

| Code | Name | `arg` | `value` / `value2` |
|---|---|---|---|
| 1 | `BOOT` | reset_cause | HardFault record of the previous run: faulting PC / CFSR (bit patterns as i32); 0 / 0 if none (OI-FW-21) |
| 2 | `STOPPED` | stop_cause (§8.2) | pos_um / pos_steps |
| 3 | `MOVE_DONE` | move_done_reason (§8.3) | final pos_um / pos_steps |
| 4 | `ESTOP_SET` | 0 | pos_um / pos_steps |
| 5 | `ESTOP_CLEARED` | 0 | 0 / 0 |
| 6 | `HALT_SET` | source: 1 PC (HALT command / Pause-Break key); 2 BUTTON never since v0.5 (D-36) | 0 / 0 |
| 7 | `HALT_CLEARED` | 0 | 0 / 0 |
| 8 | `PAUSED` | source: 1 PC, 2 BUTTON (sent when PAUSED goes 0 → 1 only) | 0 / 0 |
| 9 | `PAUSE_CLEARED` | pause_cleared_reason: 2 HALT_CLEAR, 3 RESUME (code 1 unused since ICD v0.3) | 0 / 0 |
| 10 | `RESUME_REQUEST` | 0 (PAUSE button pressed while PAUSED) | 0 / 0 |
| 11 | `FAULT_SET` | FAULT bit index (§7.6) | deciding value: raw (LOAD_LIMIT), deviation µm (HOME_DRIFT), ms the driver power stayed present with the E-stop open (K1_WELDED) / 0; else pos_um / pos_steps |
| 12 | `FAULT_CLEARED` | FAULT mask cleared | 0 / 0 |
| 13 | `LIMIT_SET` | limit_id: 0 START, 1 END | pos_um / pos_steps |
| 14 | `LIMIT_CLEARED` | limit_id: 0 START, 1 END | 0 / 0 |
| 15 | `LINK_WDG` | 0 | 0 / 0 |
| 16 | `LINK_RESTORED` | 0 | 0 / 0 |
| 17 | `VALID_CLEARED` | stop_cause (§8.2) | 0 / 0 |
| 18 | `HOMED` | 0 | drift µm vs. the previous zero (0 if not homed before) / 0 |
| 19 | `HOME_FAILED` | home_fail_reason: 1 NOT_FOUND, 2 WIRING, 3 ABORTED | pos_um / pos_steps |
| 20 | `DRIVER_ENABLED` | 0 | 0 / 0 |
| 21 | `DRIVER_DISABLED` | driver_disabled_cause: 1 PC DISABLE, 2 IDLE, 3 ESTOP, 4 DRV_POWER_LOST | 0 / 0 |
| 22 | ~~`STOP_BUTTON`~~ | **retired in v0.5**: never sent, code never reused — was: STOP/BREAK button pressed / released. D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (MCU sense, D-41) | – |
| 23 | `PAUSE_BUTTON` | 1 pressed, 0 released | 0 / 0 |
| 24 | `ALM_CHANGED` | 1 active, 0 inactive | 0 / 0 |
| 25 | `AFE_REINIT` | re-init count (low 16 bit) | 0 / 0 |
| 26 | `AFE_RATE_MISMATCH` | 1 set, 0 cleared | measured rate 0.1 SPS / 0 |
| 27 | `AFE_STALE` | 1 stale, 0 fresh again | 0 / 0 |
| 28 | `PARAMS_SAVED` | 0 | record sequence number / 0 |
| 29 | `PARAMS_LOADED` | values replaced by defaults | 0 / 0 |
| 30 | `PARAMS_DEFAULTED` | params_defaulted_reason | 0 / 0 |
| 31 | `NVM_ERROR` | nvm_detail (E_NVM detail) | 0 / 0 |
| 32 | `CLK_FALLBACK` | 0 | 0 / 0 |
| 33 | `DRIVER_POWER` | 1 power present, 0 lost (debounced; only with drv.pwr_sense_enable) | 0 / 0 |
| 34 | `NOT_SETTLED` | 0 (PEND not active within drv.pend_timeout_ms after the last pulse; warning only) | elapsed ms / 0 |

<!-- END GENERATED protocol:event -->

### 8.2 Stop causes (STOPPED arg, VALID_CLEARED arg)
<!-- BEGIN GENERATED protocol:stop_cause (gen_protocol.py) -->

Generated from `protocol.yaml` table `stop_cause` (C `SC_*`, Python `StopCause`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `NONE` | — |
| 1 | `PC_STOP` | STOP mode 0 |
| 2 | `PC_STOP_CONTROLLED` | STOP mode 1 |
| 3 | `PC_HALT` | HALT command |
| 4 | ~~`STOP_BUTTON`~~ | **retired in v0.5**: never sent, code never reused — was: physical STOP/BREAK button. D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (MCU sense, D-41) |
| 5 | `PAUSE_BUTTON` | physical PAUSE button |
| 6 | `PC_PAUSE` | PAUSE command |
| 7 | `ESTOP` | E-stop sense opened |
| 8 | `LIMIT_START` | START limit switch |
| 9 | `LIMIT_END` | END limit switch |
| 10 | `LIMIT_WIRING` | both limit inputs active |
| 11 | `LOAD_LIMIT` | FW load limit (incl. rail sample) |
| 12 | `AFE_FAULT` | AFE stale while moving |
| 13 | `LINK_WDG` | link watchdog |
| 14 | `JOG_DEADMAN` | jog dead-man (VALID unchanged) |
| 15 | `STEP_FAULT` | step overrun / count fault |
| 16 | `HOME_FAIL` | homing failure |
| 17 | `DRV_POWER_LOST` | driver power lost while a motion was running (SAF-FW-024, D-29c) |

<!-- END GENERATED protocol:stop_cause -->

### 8.3 MOVE_DONE reasons
<!-- BEGIN GENERATED protocol:move_done_reason (gen_protocol.py) -->

Generated from `protocol.yaml` table `move_done_reason` (C `MD_*`, Python `MoveDoneReason`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `TARGET` | MOVE_ABS / HOME end |
| 1 | `LOAD_THRESHOLD` | MOVE_UNTIL_LOAD threshold |
| 2 | `BOUND` | MOVE_UNTIL_LOAD or JOG bound |
| 3 | `SOFT_LIMIT` | JOG end at a soft limit or un-homed travel bound |
| 4 | `JOG_ZERO` | JOG 0 |
| 5 | `STOPPED` | ended by a stop source; see the preceding STOPPED / HOME_FAILED |

<!-- END GENERATED protocol:move_done_reason -->

Argument enums of the other EVENTs (`source`, `pause_cleared_reason`, `home_fail_reason`, `driver_disabled_cause`, `params_defaulted_reason`, `nvm_detail`, `limit_id`, `reset_cause`): Appendix B.

---

## 9. Link supervision and timing (IF-005, IF-011, SAF-FW-015, SAF-SW-003)

### 9.1 FW side (normative)
- **Link watchdog** (SAF-FW-015, D-15, D-47 a): no valid command frame (§3.1) for `safety.link_timeout_ms`
  (default 1000 ms) → in **every** motion state VALID is cleared (EVENT VALID_CLEARED, arg LINK_WDG 13, if it was
  1; v0.7.4, D-47 a: a PC unheard during a capture window must not leave VALID = 1); **while moving** (motion
  states 3–7) additionally a controlled stop (driver kept enabled), LINK_WDG set, EVENTs LINK_WDG + STOPPED
  (cause LINK_WDG); LINK_WDG clears at the next valid command frame (EVENT LINK_RESTORED); no motion restarts.
  The response to that restoring command frame MAY still carry the LINK_WDG bit (STATUS / DATA `status`): the FW
  clears it and sends LINK_RESTORED in its next 1 kHz tick, ≤ 1 ms after the frame (OBS-M4-01); receivers take
  LINK_RESTORED (or the next DATA / STATUS without the bit) as the end of LINK_WDG.
  Not moving: no stop, no LINK_WDG status bit, no LINK_WDG / STOPPED / LINK_RESTORED EVENT. The trip happens once
  per silence period. Checked at ≥ 1 kHz (reaction starts ≤ timeout + 2 ms; a silence of timeout − 1 ms never
  trips). Reference model and vectors: `ref_linkwdg.py`, `vectors/linkwdg_vectors.json` (§12).
- **Response time**: ≤ 10 ms from the last command byte to the first response byte; SAVE/LOAD/DEFAULT_PARAMS
  ≤ 2.5 s (NFR-008); commands received during a SAVE flash operation: after it (§2.4 SAVE exemption, D-37a). STOP/HALT: no further PUL edge ≤ 2 ms after the last command byte (SAF-FW-002).
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
| RETRY | DIAG_MEAS read ops (Appendix C); PING, GET_INFO, GET_STATUS, GET_PARAM, GET_ALL_PARAMS, SET_PARAM, STREAM_START, STREAM_STOP, SET_VALID, JOG 0 | response timeout 100 ms (GET_INFO at connect 200 ms); ≤ 2 retries, each with a **new SEQ** and the **newest** value the SW wants at that moment (latest wins; a retry superseded by a newer command is dropped); a late response to an abandoned SEQ is ignored |
| CONFIRM | STOP, HALT, PAUSE | priority path; repeated every 50 ms until confirmed — STOP: ACK or `MOVING = 0`; HALT: ACK or HALT flag; PAUSE: ACK or PAUSED — ≤ 20 attempts in 1 s; works with the stream off (§9.4) |
| ONCE_PRIORITY | — (unused since v0.4.1, D-34; code kept) | — |
| VERIFY | MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0, RESUME, ENABLE, DISABLE, SAVE/LOAD/DEFAULT_PARAMS, REBOOT; **HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR** (D-34; still written on the priority lane, §2.4) | **never retried automatically.** After a timeout (100 ms; SAVE/LOAD/DEFAULT 3000 ms + wire time of the TX backlog; REBOOT: reconnect after EVENT BOOT or 3 s) the SW sends GET_STATUS and decides from `motion_state`, `target_um`, latches, `sys_flags.CFG_DIRTY` / `nvm_record_seq` whether the command was executed (clears: the latch bits `flags.HALT` / `flags.ESTOP` / STATUS `faults` and `status.PAUSED`; a NACK detail is shown verbatim). JOG ≠ 0 refreshes are a stream of new commands every ≤ 100 ms (newest speed); a lost one is covered by the next refresh and by the FW dead-man. |

Why the clears are VERIFY (D-34, closes OI-ICD-08): a retry after a lost response could clear a latch that
was set **again** in between — HALT_CLEAR a new HALT (Pause/Break key or GUI HALT pressed in the ~100 ms after
the first HALT_CLEAR was sent), ESTOP_CLEAR a new E-stop event, FAULT_CLEAR a new LOAD_LIMIT trip (always
clearable while the load is still beyond the threshold, §5.5). No clear starts motion, but the operator's new
latch would disappear silently. After a timeout the SW reads GET_STATUS: latch gone and no new EVENT
HALT_SET / ESTOP_SET / FAULT_SET since → executed; latch present → shown to the operator, who clears again.

Why RESUME is VERIFY (not ONCE_PRIORITY, D-31): a retry after a lost response could clear a **new** PAUSE
pressed in the meantime, and the SW would then re-issue the target — a restart against the operator. After a
RESUME timeout the SW reads GET_STATUS: PAUSED = 0 and no EVENT PAUSED since → executed; PAUSED = 1 → the
pause stands (resume again by operator action). A motion command refused with BLOCK PAUSED (or a RESUME refused
with E_STATE) is an expected outcome: no retry, no link-degradation count (D-33k).

Why motion is never retried: a retry after a lost response could be executed after an intervening stop and
move again (since ICD v0.3 a PAUSE stop additionally blocks it, D-30, but the SW does not rely on that);
absolute arguments make an accidental duplicate harmless
(the FW answers `E_BUSY`, or a completed move to the same target is zero-length), but the SW does not rely on
it. Outstanding commands ≤ 4 (priority path exempt); ≤ 100 command frames/s.

**Link states** (SW): DEGRADED after 3 consecutive command timeouts or 300 ms without any received frame while
streaming; LOST after 1 s without frames; during motion, no DATA for > 500 ms → STOP, sequence terminated,
"LINK LOST" (SAF-SW-003). The SW never re-enables, re-homes or restarts motion automatically.

### 9.4 Confirming STOP / HALT / PAUSE with the stream off (F-B-05, SW-STOP-002)
The ACK proves reception and execution (the stop is executed before the response is sent). Without an ACK,
the SW keeps repeating (§9.3) and polls GET_STATUS: `flags.MOVING = 0` (STOP), `flags.HALT` (HALT),
`status.PAUSED` (PAUSE). An indication confirms the stop only if it was produced **after the FW received the
STOP** — ordered by device time (`t_us` of the DATA frame / STATUS ≥ the device receive time of the STOP,
estimated from the PC–device time pairing), never by PC receive stamps alone (D-37d, OBS-M1-R1). If none
arrives within 1 s the SW alarms "use the red E-stop button"; the FW link watchdog stops a motion whose PC
went silent.

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
| GET_STATUS response | 95 B | ≤ 5 Hz polling | ≤ 475 | 0.52 % |
| other responses (PING, JOG, SET_…) | 9–16 B | ≤ 20 /s | ≤ 320 | 0.35 % |
| **Total** | | | **≤ 3 355** | **≤ 3.7 %** (requirement ≤ 10 %) |

Fallback frames (10 Hz) replace DATA frames while stale. GET_ALL_PARAMS at connect: 152 + 152 + 68 B once.
PC → FW: JOG refresh 20 B × 10 Hz + PING 8 B × ≤ 7 Hz + commands ≈ 0.4 % of the link. STOP/HALT/PAUSE are
executed on arrival (stop sniffer, §2) before any response is queued, and never wait behind SW queues (§2.4).

---

## 11. Parameters (FW-CFG-001…003, FW-NVM-001/002, FW-PAR-001…006)

### 11.1 Dictionary and hash
`00_System/specs/params.yaml` is the single source of truth (schema in its header): id (stable, never
reused), key, type, unit, min/max/default, flags **M** `moving_ok`, **N** `nvm`, **R** `reboot_required`.
`gen_params.py` generates `02_FW/src/gen/params_gen.{h,c}`, `03_SW/src/bend_stand/core/params_gen.py` and
Appendix A — and, from `protocol.yaml`, the protocol name outputs of §0.3; `--check` fails on stale outputs
(IF-010). Retired ids (`retired_ids`, e.g. 0x0401 `home.ref_switch` of dict_version 1) are never reused; an NVM
record holding a retired id is migrated by id (§11.3 rule 4: the entry is dropped). **PARAM_DICT_HASH** = CRC-32 (zlib) of the canonical
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
5. The resulting image violates a hard rule → boot: all defaults + EVENT PARAMS_DEFAULTED (4); LOAD: `E_NVM` 1,
   RAM unchanged, no EVENT (OBS-M1-03).
Session parameters always start at their defaults. Flash erase/program only while idle (FW-NVM-003).

### 11.4 Hard rules (SET_PARAM → `E_CONFIG`, detail = id of the other parameter)

| Rule | Condition (must hold after the SET) | Detail for a SET of … |
|---|---|---|
| H1 | `limits.soft_min_um < limits.soft_max_um` | min → id of max; max → id of min |
| H2 | `safety.load_raw_min < safety.load_raw_max` | min → id of max; max → id of min |
| H3 | `1e9 / motion.max_step_rate_hz ≥ motion.pulse_high_ns + motion.pulse_low_min_ns` (integer form: `rate · (high + low) ≤ 1e9`) | rate → id of `pulse_high_ns`; high or low → id of `max_step_rate_hz` |
| H4 | `motion.v_max_load_um_s ≤ motion.v_max_travel_um_s` | load → id of travel; travel → id of load |
| H5 | `afe.timeout_ms ≥ 2 ×` conversion period of `afe.rate_sps` (integer form `timeout_ms · sps ≥ 2000`; SPS10 → ≥ 200 ms, SPS80 → ≥ 25 ms; D-33a, DEF-P1-01) | timeout → id of `afe.rate_sps`; rate → id of `afe.timeout_ms` |

Because rules couple parameters, the SW writes a configuration in an order that keeps every rule true after
each single SET (for min/max pairs: move the outward bound first; for H3: lower the rate before widening
pulses, widen the rate after narrowing pulses; for H5: raise `afe.timeout_ms` before switching to SPS10, switch
to SPS80 before lowering it); an `E_CONFIG` item is retried after the others in a further
pass, until a pass makes no progress (then REJECTED). **Every range end is reachable** (v0.2): the ranges of
rule partners are offset by one so a strict rule can hold at each end (`limits.soft_min_um` ≤ 399 999 <
`soft_max_um` max 400 000; `safety.load_raw_max` ≥ −7 151 120 > `load_raw_min` min; `safety.load_raw_min` ≤
+7 151 120 < `load_raw_max` max); the `set_<key>_min/_max` check vectors are all OK (with partners relaxed) and
`gen_vectors.py` asserts it.

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
    check order, one vector per motion refusal reason (SAF-FW-020), clears, NVM, driver-power loss and
    K1_WELDED (D-29c), PAUSED blocks / kept / cleared only by RESUME or HALT_CLEAR (`expect.paused_after`,
    D-30/D-31), RESUME refusals (OBS-P1-15), and per parameter
    default/min/max/below/above/NaN/type/padding/while-moving (FW-CFG-003), hard rules H1–H5. Every vector state
    is a valid configuration (all hard rules hold; asserted by the generator). `state_schema` (= 2) versions
    the state keys: keys are only added, never renamed or removed, and every addition bumps it (F-B-25).
  - `vectors/units_vectors.json` (v0.4, OI-FW-20, M1): µm → steps, steps → µm and step-rate speed cap for
    8 steps/mm values incl. ties (§0.1).
  - `vectors/motion_vectors.json` (M2, OI-FW-20; definitions in `00_System/tools/ref_motion.py`): step periods
    of position moves (R4 TV-M trapezoid / triangle / asymmetric, defaults, slow move), controlled stops in
    cruise / acceleration / near the end (stop sized `r0 = ceil(v²/2a_stop)`, never faster than the current
    period, so the deceleration never exceeds `motion.a_stop_um_s2`), JOG on-the-fly speed changes (speed-up
    resumes the accel index from the exact current speed), the §6.5 stop-path rule (CLEAN / ISR / STRETCH) and
    the planner; FW float32 tolerance ±1 tick per period, sum **±ceil(N/1000)** ticks (D-40b; SRS FW-MOT-003
    aligned); the SW simulator matches exactly.
  - `vectors/loadlim_vectors.json` (v0.6, D-40d; definitions in `ref_loadlim.py`): FW load-limit sample sequences
    (trip, trip samples, rails, regrow window open / unload / end inside / new reference / config / at the rail)
    with `trip` and `regrow_window` after every step.
  - `vectors/linkwdg_vectors.json` (v0.7.4, D-47 a; definitions in `ref_linkwdg.py`): link watchdog per motion
    state (NOT_ENABLED, IDLE, MOVE_ABS, MOVE_UNTIL_LOAD) × VALID before × timeout (default, minimum) × silence
    (timeout − 1 ms: no trip; timeout + 2 ms: trip): stop, VALID after, LINK_WDG status, the EVENT set at the trip
    and at the next valid command frame. Replayed against the twin by `03_SW/tests/integration/test_twin_m4_linkwdg.py`.
  - `check_vectors.json` `hw_meas_vectors` (v0.6): DIAG_MEAS acceptance in a HW_MEAS build (op / field ranges,
    MEAS_STATE); the main `vectors` hold the release / twin verdict (NOT_IN_BUILD) and the LIMIT_WIRING clear rule
    (D-40a).
- `00_System/tools/gen_params.py` — dictionary generator (§11.1); also runs `gen_protocol.py` (protocol name
  registry `protocol.yaml` → `proto_gen.h`, `protocol_gen.py`, generated ICD tables, §0.3).
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
| D-29 (a) / (b) / (c) / (d) | §5.5 + §7.2 / §5.4 + App. A / §6.2 + §7.6 / §6.5 |
| D-30 (PAUSED blocks motion, clean-halt condition) | §4.3, §5.4, §5.5, §6.2, §6.3, §6.5 |
| D-31 (RESUME 0x3C) | §3.2, §4.3, §5.5, §6.3, §9.3, App. B |
| D-36 / CR-01 (no physical holding STOP) | §5.5, §6.2, §6.3, §6.4, §7.6, §8, App. A/B |
| D-37 (a) / (b) / (c) / (d) | §2.4 + §9.1 / §7.6 / §2.4 / §9.4 |
| D-40 (a) / (b) / (c) / (d) | §5.5 + §6.2 / §12 / §5.6 + App. C / §5.5 |
| D-41 / D-42 (CR-03) / D-43 (b) | §6.2 + App. A (`drv.pwr_sense_enable` 0) / §6.2 / §5.4 + check vectors |
| SRS OI-18 (Orchestrator, v0.7) | §6.2 + App. A (`drv.k1_check_enable` 0x0706, default 0, R) |
| D-33 (a) / (f) / (g) / (h) / (k) | §11.4 + App. A / §6.2 / §6.2 + §7.2 / §6.2 / §9.3 |
| OI-FW-17 / 18 / 19 / 20 / 21 / 22 / 23 | `tools/README.md` seams / §2.4 / §2 / §0.1 + §12 / §4.2 + §6.4 / §6.4 / §5.4 |
| GF-01 / GF-08 / OI-FW-11 | §5.5, §7.2 / §0.3, App. B / §0.3, App. B |

## 14. Open issues and SRS deltas

| ID | Item | Owner / proposal |
|---|---|---|
| SD-01 | *(accepted D-29a)* **PAUSE command** added to IF-012 (Orchestrator decision on F-B-03): PAUSED rules superseded by D-30 (SD-13): blocks motion, cleared by RESUME or HALT_CLEAR (D-31). | Orchestrator: SRS v0.3 (IF-012, §3.2 row "GUI Pause", SAF-FW-023). |
| SD-02 | *(accepted D-29c; K1 time 200 ms = `drv.k1_weld_ms`)* R5 / D-28 interface items: status DRV_PWR, faults K1_WELDED + HOME_DRIFT, BLOCK bits DRV_UNPOWERED + DRIVER_ALARM, EVENT DRIVER_POWER / NOT_SETTLED, stop cause DRV_POWER_LOST; parameters `drv.pend_timeout_ms`, `drv.pwr_sense_enable`, `home.drift_tol_um`; `motion.ena_settle_ms` default 500. | Orchestrator: SRS v0.3 (§3.2 rows, FW-SW-004, FW-PAR-002/006, Table 5.1). |
| SD-03 | *(accepted D-29e)* `motion.v_max_um_s` (Table 5.1) replaced by `motion.v_max_travel_um_s` (30 mm/s) + `motion.v_max_load_um_s` (20 mm/s) with hard rule H4 (R5 §2.3, D-28). | Orchestrator: Table 5.1, FW-PAR-003, FW-MOT-009. |
| SD-04 | *(accepted D-29f; v0.2 update)* Renames: `input.release_ms` → `io.release_ms`, `input.estop_release_ms` → `io.estop_release_ms`. `home.switch` (v0.1: `home.ref_switch`) is **removed** in dict_version 2 (homing only at START, D-29b; id 0x0401 retired). | Orchestrator: Table 5.1 — delete the `home.switch` row; FW-HOM-001 "fast seek toward START". |
| SD-05 | *(accepted D-29f)* Session parameters `safety.zero_raw`, `safety.load_raw_min/max` are not persisted (`nvm: false`). | Orchestrator: confirm (SAF-SW-002 rewrites them at connect). |
| SD-06 | *(accepted D-29f)* `motion.pul_invert`, `motion.ena_invert`, `drv.pwr_sense_enable` are `reboot_required` (a live change could emit a step edge or release a holding driver). | Orchestrator: FW-MOT-001 note. |
| SD-07 | *(accepted D-29f)* JOG carries an optional absolute `bound_um` (F-B-15); "no bound" is `0x80000000`, not 0 (0 can be a valid position when `soft_min_um ≤ 0`). MOVE_UNTIL_LOAD direction is implicit in the absolute `bound_um`; the comparison sense is the explicit `cmp` field. | Orchestrator: FW-MOT-005/006 wording. |
| SD-08 | *(accepted D-29f)* Motion commands are never auto-retried (VERIFY class, F-B-01); the FW has no duplicate acknowledgement. | Orchestrator: IF-005 wording ("retries only idempotent commands"). |
| SD-09 | *(accepted D-29f)* FW_design inputs: `motion.max_step_rate_hz` max **100 kHz** (SRS cap 200 kHz, OI-FW-04); `motion.steps_per_mm` min **100** (SRS proposed 1, OI-FW-05; SRS minimum range 100…10 000 still met); MAX_LEN 160 / 20 parameters per page (OI-FW-02). | Orchestrator: FW-MOT-001, FW-MOT-009, Table 5.1. |
| SD-10 | *(accepted D-29f)* CLK_FALLBACK (requested as a DATA status bit, OI-FW-11) is reported in GET_STATUS `sys_flags` and EVENT CLK_FALLBACK only: DATA `status` has no free bit and the condition is fixed from boot. | Orchestrator / A: accept. |
| OI-ICD-01 | **Closed (D-29b):** homing only at START; END-homing semantics and `home.ref_switch` removed (v0.2). | — |
| OI-ICD-02 | Count-based defaults/ranges depend on the nominal 32 212 counts/kg (OI-07). | Re-derive after the first calibration (dict_version bump). |
| OI-ICD-03 | `HOME_ABORTED` (FW-HOM-002) is reported as HOME_FAILED reason ABORTED, not as a latched fault (the stopping cause has its own latch). | Orchestrator: confirm. |
| SD-11 | v0.2 additions for SRS v0.3: STATUS `pause_src` (GF-01); PAUSED rules superseded by D-30 (SD-13); parameter `drv.k1_weld_ms` (default 200, 100…2000 ms, D-29c); DRIVER_POWER (0) on every loss, STOPPED DRV_POWER_LOST only if a motion was running, DRIVER_DISABLED arg 4 only if not already NOT_ENABLED; ALM start-block scope (§4.3); §6.5 clean halt. | Orchestrator: SRS v0.3 (SAF-FW-023, SAF-FW-005, SAF-FW-003, FW-PAR-006, Table 5.1). |
| SD-12 | v0.2 range changes so every range end is reachable: `limits.soft_min_um` max 399 999; `safety.load_raw_max` min −7 151 120; `safety.load_raw_min` max +7 151 120. | Orchestrator: Table 5.1 (if it lists these ranges). |
| OI-ICD-04 | **Closed (D-30):** PAUSED blocks every new motion start incl. jog refreshes (BLOCK PAUSED) and is cleared only by HALT_CLEAR, so an in-flight JOG / MOVE cannot restart motion after a PAUSE. | — |
| SD-13 | **D-30 SRS delta** (v0.4: with D-31 the clear set is RESUME or HALT_CLEAR, Resume = RESUME + re-issue): SAF-FW-023 / §3.2 PAUSE row / FW-MOT-007 / SW-STOP-004: "PAUSED clears on the next accepted motion command or HALT_CLEAR" → "PAUSED blocks MOVE_ABS, MOVE_UNTIL_LOAD, HOME and JOG ≠ 0 (incl. refreshes) with E_STATE PAUSED and is cleared only by HALT_CLEAR; Resume = HALT_CLEAR + re-issue the absolute target"; SAF-FW-020 refusal list + PAUSED; SAF-FW-003 note + "and the planned stop distance ≤ 1 step". | Orchestrator: SRS v0.4. |
| OI-ICD-06 | **Closed (SRS v0.4):** SAF-FW-024 now sends STOPPED (DRV_POWER_LOST) only when a move was ended, as ICD §6.2. | — |
| OI-ICD-05 | **Closed (D-30):** clean halt only when the step period > 2 ms **and** the FW-computed stop distance ≤ 1 step (§6.5). | — |
| OI-ICD-07 | **Closed:** FW_design §5.6.4 reprograms the running step period immediately (extend-only, race guard), so deceleration starts ≤ 2 ms after the trigger also at a step period > 2 ms. | — |
| SD-14 | **D-31 / D-33 SRS deltas:** IF-012 + RESUME (0x3C); SAF-FW-023 RESUME semantics; IF-005 RESUME never auto-retried; SAF-FW-012 hard rule H5 and `afe.timeout_ms` default 250 ms (Table 5.1: min 25, default 250); SAF-FW-017 no idle disable while stale; SAF-FW-013 limit latch clear = released ≥ `io.release_ms`, only motion away while latched; SAF-FW-024/025 timing bounds (NVM operation + 25 ms; K1 ≤ `k1_weld_ms` + 25 ms); FW-MOT-006 bound = position → E_RANGE. | Orchestrator: SRS v0.4. |
| F-B-06 / F-B-25 / F-B-28 / F-B-30 / F-B-32 / F-B-33 | **Closed (v0.4):** shared sim/twin vocabulary v2 frozen (`tools/README.md`); `state_schema` 2 + stable keys; MOVE_UNTIL_LOAD bound = position → `E_RANGE` 0; RESUME (D-31) refused only by ESTOP (latched or input open), HALT and latched faults, never by NOT_ENABLED, DRV_UNPOWERED, DRIVER_ALARM, LIMIT, AFE or motion state, retry class VERIFY; H5 with partner-id detail and write order (§11.4), `motion.steps_per_mm` default 800 (D-27 closed). | — |
| OI-ICD-08 | **Closed (D-34, v0.4.1):** HALT_CLEAR, ESTOP_CLEAR and FAULT_CLEAR are VERIFY (never auto-retried; priority lane), so a retry can no longer clear a latch set again in between (§9.3). | — |
| SD-15 | **CR-01 / D-36 + D-37 SRS deltas** (SRS v0.5): SAF-FW-022 withdrawn; FW-SW-003 STOP part removed; HALT source PC only; `io.stop_active_level` retired; IF-011/NFR-008 SAVE exemption (D-37a); status-bit validity by feature bit (D-37b); STOP confirmation ordered by device time (D-37d). | Orchestrator: SRS v0.5 (applied in parallel). |
| OBS-M1-01…05 | **Closed (v0.5):** SAVE exemption §2.4 (01); twin RX during flash stalls fixed (02, `tools/README.md`); LOAD sends no EVENT on failure §5.2 / §11.3 (03); unit-conversion saturation §0.1 (05). | — |
| IF-C-M1-02 | **Closed (v0.5):** §7.6 feature-dependent bits. Follow-ups: A sends DRV_PWR = 0 while FEAT_DRV_SIGNALS = 0 also with `drv.pwr_sense_enable` = false; B's simulator masks the bits by its feature mask. | A, B |
| OI-ICD-09 | **Closed (v0.6):** A aligned `ramp_stop()` / `ramp_set_speed()` (REQ-A-M2-06); Validator E dry run 9/9 + 28/28, Integrator differential re-run 2026-10-04. | — |
| OI-ICD-10 | **Closed (v0.7):** A confirmed Appendix C (OI-FW-38); Appendix C updated with the FW facts (ring 2048, stimulus clock 10 MHz, PROBE_READ w1 = 0 / w5 = PUL stamps since arming / TRIGGERED = counter running, STIM delay span = running step period else 1 ms, STATIC_LEVEL only with the step timer stopped); DMA map ASSUMED until HG-29. | — |
| OI-B-M2-03 | **Answered (v0.7):** only a FAULT_CLEAR that clears a latched LOAD_LIMIT takes a new load reference; other clears leave the load-limit state unchanged. A's FW (`cmd.c` FAULT_CLEAR → `afe_loadlim_cleared()` only when LOAD_LIMIT was cleared; reference 0 without a sample) matches; `ref_loadlim.py` and `loadlim_vectors.json` aligned (new cases `regrow_clear_without_latch_keeps_reference`, `regrow_new_reference_after_retrip`). | B: align the simulator |
| OI-FW-43 | **Closed (v0.7.2 text, v0.7.3 closed):** MOVE_UNTIL_LOAD execution rules (a)–(e) in §5.4 and the threshold row in §6.2, as built by A (FW_design v0.7 §5.4.4); vectors `mul_*` check vectors, `immediate_stops`, `threshold_in_controlled_stop` replayed by A's native suite (176/176 per A) and the twin tests `test_twin_m3_mul.py` | B: simulator mirrors (a)–(d) (MC2-6 section `mul43`) |
| OI-B-M2-04 | **Answered (v0.7, tools/README):** coordinate convention of the twin world and the simulator: x [µm] = world position integrated per completed pulse (pulse end) from the DIR pin; START active iff x ≤ `start_switch_um`, END active iff x ≥ `end_switch_um`, evaluated after every completed step; the HAL fixed reaction stops CLEAN at that step: the last executed step is the first one at which the switch is active (step-exact, no extra step). Machine x after HOME = x_world − x_edge − `home.offset_um`, x_edge = world x of that first active step on the slow approach. | B: same convention in the simulator |
| OI-FW-35 | **Closed (v0.7):** the twin no longer calls `step_isr()` after a HAL fixed-reaction halt at a counted step (twin_seams.c). | — |
| MC2-2 / OI-F-M2-03 | **Closed (v0.7):** §9.3 rationale reworded (Pause/Break-key HALT). | — |
| SD-18 | **D-47 a SRS delta** (v0.7.4): SAF-FW-015 to read "no valid frame for `safety.link_timeout_ms` clears VALID in every motion state (EVENT VALID_CLEARED); while moving additionally …" (today: "while moving (any mode) … clear VALID"); §6 budget row unchanged. | Orchestrator (SRS) |
| OI-C-M4-01 / OI-C-M4-02 | **Closed (v0.7.4, D-47 b / a):** M4 twin `specimen` vocabulary entered with the version bump; link watchdog clears VALID in every motion state (§4, §6.2, §9.1, `linkwdg_vectors.json`). | A: FW done (`safety.c`, native 177/177); B: simulator done (`io/sim/board.py`); E/F: replay `linkwdg_vectors.json` |
| SD-17 | **CR-03 / D-41…D-43 SRS deltas** (SRS v0.6): E-stop MCU/FW only + hardwired ENA cut, FW tolerates the forced ENA; K1_WELDED / DRV_PWR optional; un-homed travel window from a latched origin; new parameter `drv.k1_check_enable` (0x0706, bool, default 0, reboot required; gates K1_WELDED, needs `drv.pwr_sense_enable`; SRS OI-18) — Table 5.1 row. | Orchestrator |
| SD-16 | **D-40 SRS deltas:** SAF-FW-014 (LIMIT_WIRING clear rule, aligned in SRS), FW-MOT-003 (±ceil(N/1000), aligned), SAF-FW-011 regrow window end (§5.5), CR-02 DIAG_MEAS / FEAT_HW_MEAS (SYS-009, NFR-007 HW-gate evidence). | Orchestrator: SRS v0.5.x |

## 15. Change history

| ICD | Date | PROTO / PAYLOAD / dict | Change |
|---|---|---|---|
| 0.7.4 | 2026-10-05 | 1.0 / 1 / 6 | **M4 integration (D-47), no wire change, dictionary unchanged (dict_version 6, hash 0xF8BCDCB8).** (a) OI-C-M4-02: the link-watchdog timeout clears VALID in **every** motion state (EVENT VALID_CLEARED, arg LINK_WDG, if VALID was 1); the controlled stop, LINK_WDG status and the LINK_WDG / STOPPED / LINK_RESTORED EVENTs stay moving-only (§4 SET_VALID automatic clear, §6.2 new row, §9.1); reference `ref_linkwdg.py` + new `vectors/linkwdg_vectors.json` (32 cases: NOT_ENABLED / IDLE / MOVE_ABS / MOVE_UNTIL_LOAD × VALID × timeout 1000 / 200 ms × silence timeout − 1 / + 2 ms); SRS delta SD-18 (SAF-FW-015). (b) OI-C-M4-01: shared vocabulary v2 additions for the M4 twin (tools/README): `specimen` `side` (pull / push / both: compression side), `k3_n_per_mm3` (cubic non-linearity), `break_travel_um` (break at a deflection), `break_residual_pct` (force kept after a break), `slip_at_n` + `slip_mm` (grip slip, names as the simulator's B6-17 extra); `query world` `specimen_broken`; seam-log entries `specimen_break` / `specimen_slip`; break and slip are world states over MCU resets (twin; the simulator supports the same arguments, SWC-M4-01 closed by B). §9.1 clarification (Validator E OBS-M4-01, same version, text only): the response to the link-restoring frame may still carry LINK_WDG; LINK_RESTORED follows ≤ 1 ms later (next 1 kHz tick). Generated files regenerated (`proto_gen.h`, `protocol_gen.py`: ICD version string only); all vector files regenerated (icd_version). |
| 0.7.3 | 2026-10-05 | 1.0 / 1 / 6 | **D-45 e (OI-E-HG-05), dict_version 6, hash 0xF8BCDCB8, no wire change:** defaults `motion.pulse_high_ns` and `motion.pulse_low_min_ns` 10 000 → **12 500 ns**, `motion.max_step_rate_hz` 50 000 → **40 000 Hz** (margin over the HBS86H 10 µs minimum; H3: 1e9 / 40 000 = 25 000 ≥ 12 500 + 12 500; 30 mm/s at 800 steps/mm = 24 kHz stays reachable, STATUS `v_limit_um_s` at the defaults = 30 000, step-rate cap 50 mm/s). Vectors regenerated; H3 check vectors re-based on the new defaults (`rule_h3_rate` 40 001 Hz, new `rule_h3_width_edge` 12 501 ns, new `rule_h3_old_rate_ok` 50 kHz with 10 + 10 µs). Twin DIAG_MEAS model fidelity from Validator E's HIL dry run (tools/README, no FW change): OBS-E-HG-01 RX probe triggers at the start bit of the byte; OBS-E-HG-02 STIM_RUN without the extra idle gap (next delay starts at the end of the hold); OBS-E-HG-03 captures of zero-latency reactions report ≥ 1 tick (the zero-latency reaction itself documented); OBS-E-HG-05 / DEF-HG-01 PWM input as on the target: first capture after arming discarded, min / max / count from the second rising edge. OI-FW-43 closed (§14). |
| 0.7.2 | 2026-10-05 | 1.0 / 1 / 5 | **M3 (D-44), no wire change.** §6.2: POS_UNCERTAIN at an E-stop edge = step output running at the edge (MC2-6 finding SWC-M3-01; A's FW unchanged, simulator to align). Vocabulary v2 additions for the M3 calibration flows (tools/README, side T, simulator optional): `weight` (`kg` \| `n`, `g_mps2`: known masses on the cell), `afe` `drift_counts_per_s` / `creep_pct` + `creep_tau_s` / `nonlin_pct_fs` + `fs_n` (R2 §3 cell figures), `specimen` `relax_pct` + `relax_tau_s` now modelled; `query world` adds `specimen_n`, `weight_n`, `relax_n`, `creep_counts`, `drift_counts`, `raw_ideal`. §5.4 MOVE_UNTIL_LOAD execution rules (a)–(e) and a §6.2 row for the threshold stop (OI-FW-43, A's FW_design v0.7 §5.4.4 / §9.10). Vectors: 21 MOVE_UNTIL_LOAD check vectors (incl. a bound within one step) (argument edges, every refusal reason, already-beyond) ; motion vectors: case `mul_to_bound_5mm` and the new list `immediate_stops` (3 MOVE_UNTIL_LOAD threshold stops = prefixes of a base case, no ramp-down) and `threshold_in_controlled_stop` (1 entry: a threshold sample cuts the controlled stop of the new case `mul_stop_in_cruise`, reason STOPPED, OI-FW-43 b); a separate list so the replays of `cases` are unaffected). tools/README coordinate convention clarified: world travel per pulse = 1000 / the world's steps_per_mm (mechanics), independent of the board parameter (SWC-M3-03). Twin / integration (OBS-M2-09): per-run private twin binary (`build.ensure_built_private`, `Twin(private=True)` copies a shared exe), atomic exe install; backend ⇄ twin and SIM ⇄ twin runs in one lock-step virtual time; SIM-vs-twin comparison rule (tools/README). |
| 0.7.1 | 2026-10-04 | 1.0 / 1 / 5 | **Appendix C only (OI-FW-41, no wire change):** DIAG_MEAS NOINIT w4 prev_valid, w5 previous last PUL, w6 previous heartbeat, w7 previous hang start, w8 boot counter (sel 1 clears all); op 9 DWT = per-section statistics with table C.1 (23 sections 0…22; count / min / max / 64-bit sum / 10 log2 bins), INFO w0 = MEAS \| DWT and w6 = empty-pair overhead in DWT builds (the "main-loop section" wording withdrawn); twin DIAG_MEAS model mirrors w4…w8 (REQ-C-M2-12); `fw_twin/build.py` prints absolute paths for a build dir outside the repo. |
| 0.7 | 2026-10-04 | 1.0 / 1 / 5 | **M2 close-out.** CR-03 / D-41 / D-42: `drv.pwr_sense_enable` default **0** (dict_version 5), new `drv.k1_check_enable` (0x0706, default 0, reboot; gates K1_WELDED, SRS OI-18), K1_WELDED / DRV_PWR / DRV_UNPOWERED texts "only with the optional power sense", E-stop = MCU / FW only + hardwired ENA cut, FW tolerates the externally forced ENA (§6.2); D-43 b un-homed travel window from a latched origin (§5.4, check vectors, state_schema 3: `unhomed_origin_um`); MC2-2 §9.3 rationale (no STOP-button HALT); OI-B-M2-03 load reference only at a LOAD_LIMIT clear (§5.5, `ref_loadlim.py`, vectors); OI-B-M2-04 coordinate convention (tools/README); Appendix C aligned to A's FW facts (OI-FW-38 / OI-ICD-10 closed); twin: DIAG_MEAS NOINIT magic at boot + 10 kHz heartbeat (REQ-C-M2-12), no `step_isr()` after a fixed-reaction halt (OI-FW-35), 4 stop-timing integration tests un-skipped. |
| 0.6 | 2026-10-04 | 1.0 / 1 / 4 | **D-40 / Validator E M2 requests.** REQ-C-M2-01: command **DIAG_MEAS 0x3D** (LEN 8, 64-byte body, 10 ops, Appendix C), INFO feature bit 9 **FEAT_HW_MEAS**, BLOCK bit 11 **MEAS_STATE**, §4.4 step 2a NOT_IN_BUILD, §5.6, tables `meas_*` in `protocol.yaml`, seam v1.3 `hal_meas_cmd()` (+ SR-M2-01 `hal_step_set_dir` ±2 encoding, SR-M2-02 `hal_in_cfg_t` without `stop_active_level`). D-40a LIMIT_WIRING clear rule (§5.5, §6.2, vectors). D-40b sum tolerance ±ceil(N/1000) stated (§12). D-40d load-limit regrow window (§5.5) + `ref_loadlim.py` / `loadlim_vectors.json`. Twin: REQ-C-M2-02 `inject loop_load`, -05 conversions carry gain / rate, -06 world x from PUL + DIR pin (`driver dir_wiring_inverted`, x persists across resets), -07 automatic PEND, -08 DIAG_MEAS model (`--hw-meas`), -09 `stop=` removed, -10 `log_max` 1 000 000 + `query clear`. OI-ICD-09 closed; OI-ICD-10, SD-16 added. Dictionary unchanged. |
| 0.5 | 2026-10-04 | 1.0 / 1 / 4 | **CR-01 / D-36** (one red button = E-stop with power cut; no physical holding STOP): DATA/STATUS `status` bit 9 and `io` bit 3 STOP_BTN, EVENT 22 STOP_BUTTON and stop cause 4 STOP_BUTTON **retired** (reserved, never reused; generated identifiers kept and marked RETIRED for compatibility, `RETIRED_MASK`); HALT source PC only; `io.stop_active_level` (0x0603) retired → **dict_version 4, 47 parameters**; SAF-FW-022 path removed (§5.5 HALT/HALT_CLEAR, §6.2 row, §6.3, §6.4); HALT_CLEAR never refused. **D-37**: (a) SAVE exemption + SW quiesce during SAVE (§2.4, §9.1); (b) feature-dependent status/IO bits sent as 0 and invalid while the feature bit is 0 (§7.6, `protocol.yaml` `feature:`, Python `<ID>_FEATURE`); (c) RESUME on the normal lane (§2.4); (d) STOP confirmation by device time (§9.4). **Queue**: OBS-M1-03 LOAD failure sends no EVENT (§5.2, §11.3); OBS-M1-05 µm/steps saturation (§0.1, `units_vectors.json` saturation cases); ESTOP_CLEAR with the input open but no latch = `E_CAUSE_ACTIVE` 0xFFFF (vector); `state_schema` stays 2 (STOP-button keys kept, ignored, never set); seam semantics + `afe_sample_t.status` bits + seam v1.2 `hal_fault_record()` in `tools/README.md`; OBS-M1-02 twin RX during flash stalls fixed; vocabulary: STOP-button inputs removed. **M2 start**: `motion_vectors.json` + `ref_motion.py` (§12), OI-ICD-09. |
| 0.4.1 | 2026-10-03 | 1.0 / 1 / 3 | **D-34**: HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR move from ONCE_PRIORITY to retry class **VERIFY** (never auto-retried; still on the SW priority lane; lost response resolved by GET_STATUS) — `protocol.yaml` retry class, §3.2 (generated), §9.3 table + rationale; ONCE_PRIORITY kept as an unused code. OI-ICD-08 closed (D-34); OI-ICD-06 closed (SRS v0.4 SAF-FW-024: STOPPED only when a move was ended). No wire, layout or dictionary change (hash unchanged). |
| 0.4 | 2026-10-03 | 1.0 / 1 / 3 | Final P1 round. **D-31** RESUME 0x3C (clears only PAUSED; E_STATE ESTOP/HALT/FAULT; retry class VERIFY; not sniffed; PAUSE_CLEARED arg 3); HALT_CLEAR clears HALT + PAUSED; §5.5, §6.3 RESUME row, §9.3 rationale. **D-33** (a) `afe.timeout_ms` default 250 ms, min 25, hard rule **H5** (dict_version 3); (f) DRV_PWR / K1 timing bounds; (g) no idle disable while stale; (h) limit latch clear rule; (k) PAUSED refusal = expected outcome. **FW_design**: OI-FW-18 wire order DATA > responses > EVENT + drop order (§2.4); OI-FW-19 sniffer STOP/HALT/PAUSE (`sniffed` in `protocol.yaml`, `CMD_IS_SNIFFED`); OI-FW-20 µm↔steps arithmetic (§0.1) + `units_vectors.json`, motion vectors planned M2; OI-FW-21 `internal_detail` (NOT_IN_BUILD, INVARIANT) + HardFault record in BOOT; OI-FW-22 boot ENA disabled with DRV_PWR off; OI-FW-23 homing bounds (constants `HOME_RELEASE_MAX_UM`, `HOME_SLOW_EXTRA_UM`); OI-FW-17 / DEF-P1-02 seam v1 = FW_design §8.1 in `tools/README.md`. **DEF-P1-03** twin world-control/observation vocabulary v2 (`tools/README.md`, M1 subset marked, shared with the SW simulator). **F-B-28** MOVE_UNTIL_LOAD bound = position → E_RANGE 0; **F-B-25** `state_schema` 2. OI-ICD-07 closed; SD-14, OI-ICD-08 added. Vectors: RESUME / H5 / F-B-28 / BOOT-HardFault / E_INTERNAL cases; generator asserts valid vector states. |
| 0.3 | 2026-10-03 | 1.0 / 1 / 2 | **D-30**: PAUSED becomes a motion-blocking latch — new BLOCK bit 10 PAUSED (`protocol.yaml`); MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0 incl. refreshes refused while PAUSED; cleared **only** by HALT_CLEAR (PAUSE_CLEARED arg 1 MOTION_CMD removed, code 1 unused); Resume = HALT_CLEAR + re-issue the absolute target (§4.3, §5.4, §5.5, §6.2, §6.3 new PAUSED column). §6.5 clean halt only with step period > 2 ms **and** FW-computed stop distance ≤ 1 step. OI-ICD-04/05 closed; SD-13, OI-ICD-07 added. Vectors regenerated (PAUSED check vectors now E_STATE PAUSED with `paused_after`, `move_abs_paused_nack`, EVENT PAUSE_CLEARED arg 2). Dictionary unchanged (dict_version 2). |
| 0.2 | 2026-10-03 | 1.0 / 1 / 2 | D-29 / GF-01 / GF-08 reconciliation. (1) **D-29b** homing only at START: HOME text rewritten (START edge at −`home.offset_um`, END reached = HOME_WIRING), END-homing semantics and `home.ref_switch` removed (id 0x0401 retired, dict_version 2); OI-ICD-01 closed. (2) **D-29a / GF-01** PAUSED latch made normative (§5.5): set/kept/cleared rules, clear set = accepted MOVE_ABS / MOVE_UNTIL_LOAD / HOME / JOG ≠ 0 or accepted HALT_CLEAR (also when HALT is not latched); STATUS grows to **86 B** (`pause_src` at 84, reserved at 85); PAUSED EVENT arg = source. (3) **D-29c** driver power: K1_WELDED after `drv.k1_weld_ms` (new parameter 0x0705, default 200 ms), driver power lost with the E-stop closed → immediate stop, NOT_ENABLED, HOMED cleared, motion refused; §6.2 rows + paragraph. (4) **D-29d** §6.5 controlled stop as clean halt at step period > 2 ms. (5) **GF-08 / OI-FW-11** `protocol.yaml` = single source of names/codes; generator emits `02_FW/src/gen/proto_gen.h`, `03_SW/src/bend_stand/core/protocol_gen.py`, the generated tables of §3.2, §4.2, §4.3, §6.1, §7.1, §7.2, §7.6, §8 and Appendix B (§0.3). (6) Unreachable range ends fixed (§11.4, SD-12). (7) SD-01…SD-10 marked accepted (D-29); new SD-11, SD-12, OI-ICD-04…06. (8) Cross-check with SRS v0.3: ALM start-block scope stated (§4.3, SAF-FW-026: running-jog refreshes, JOG 0, STOP/HALT/PAUSE, ENABLE not blocked; reference model fixed accordingly), driver-power loss row aligned to SAF-FW-024 (any cause, ≤ 25 ms, settle from the later of ENABLE and power return). Vectors regenerated (STATUS layout, PAUSED, driver-power, K1 events/checks). PROTO_VERSION stays 1.0 (pre-baseline, §0.2). |
| 0.1 | 2026-10-03 | 1.0 / 1 / 1 | Initial version from SRS v0.2, D-01…D-28, R3 §1.6, R4 §9, R5 §8, SW_design F-B-01…06/15/19, FW_design OI-FW-02/04/05/11: framing/CRC/parser (Thrust_Stand origin, MAX_LEN 160), 25 commands incl. PAUSE, DATA v1 18 B, EVENT 16 B, STATUS 84 B, INFO 44 B, BLOCK/FAULT/IO masks, state model, retry classes, 48-parameter dictionary, vectors. |

---

## Appendix A. Parameter dictionary (informative — generated; normative source: `params.yaml`)

<!-- BEGIN GENERATED PARAM TABLE (gen_params.py) -->

Generated from `params.yaml` dict_version 6 — **PARAM_DICT_HASH = 0xF8BCDCB8**, 48 parameters. Flags: **M** = moving_ok (settable while moving), **N** = nvm (persisted), **R** = reboot_required. Normative descriptions: `params.yaml`.

| ID | Key | Type | Unit | Min | Max | Default | Flags | Values / notes |
|---|---|---|---|---|---|---|---|---|
| 0x0101 | `afe.gain_channel` | enum |  |  |  | A128 | N | 0=A128, 1=B32, 2=A64 |
| 0x0102 | `afe.rate_sps` | enum |  |  |  | SPS80 | N | 0=SPS10, 1=SPS80 |
| 0x0103 | `afe.rate_tol_pct` | u8 | % | 5 | 50 | 20 | MN |  |
| 0x0104 | `afe.settle_discard` | u8 | samples | 0 | 20 | 4 | MN |  |
| 0x0105 | `afe.timeout_ms` | u16 | ms | 25 | 1000 | 250 | MN |  |
| 0x0201 | `motion.steps_per_mm` | f32 | steps/mm | 100 | 100000 | 800 | N |  |
| 0x0202 | `motion.pul_invert` | bool |  |  |  | false | NR |  |
| 0x0203 | `motion.dir_invert` | bool |  |  |  | false | N |  |
| 0x0204 | `motion.ena_invert` | bool |  |  |  | false | NR |  |
| 0x0205 | `motion.pulse_high_ns` | u32 | ns | 2500 | 100000 | 12500 | N |  |
| 0x0206 | `motion.pulse_low_min_ns` | u32 | ns | 2500 | 100000 | 12500 | N |  |
| 0x0207 | `motion.max_step_rate_hz` | u32 | Hz | 100 | 100000 | 40000 | N |  |
| 0x0208 | `motion.dir_setup_us` | u16 | us | 5 | 1000 | 20 | N |  |
| 0x0209 | `motion.ena_settle_ms` | u16 | ms | 0 | 2000 | 500 | N |  |
| 0x020A | `motion.v_max_travel_um_s` | u32 | um/s | 1 | 250000 | 30000 | N |  |
| 0x020B | `motion.v_max_load_um_s` | u32 | um/s | 1 | 250000 | 20000 | N |  |
| 0x020C | `motion.a_max_um_s2` | u32 | um/s2 | 1000 | 10000000 | 100000 | N |  |
| 0x020D | `motion.a_stop_um_s2` | u32 | um/s2 | 10000 | 10000000 | 1000000 | N |  |
| 0x020E | `motion.v_unhomed_um_s` | u32 | um/s | 1 | 20000 | 2000 | N |  |
| 0x020F | `motion.jog_timeout_ms` | u16 | ms | 50 | 1000 | 250 | MN |  |
| 0x0301 | `limits.soft_min_um` | i32 | um | -10000 | 399999 | 500 | N |  |
| 0x0302 | `limits.soft_max_um` | i32 | um | 0 | 400000 | 290000 | N |  |
| 0x0402 | `home.v_fast_um_s` | u32 | um/s | 10 | 20000 | 5000 | N |  |
| 0x0403 | `home.v_slow_um_s` | u32 | um/s | 10 | 20000 | 500 | N |  |
| 0x0404 | `home.a_um_s2` | u32 | um/s2 | 1000 | 1000000 | 50000 | N |  |
| 0x0405 | `home.backoff_um` | u32 | um | 0 | 20000 | 2000 | N |  |
| 0x0406 | `home.offset_um` | u32 | um | 0 | 20000 | 1000 | N |  |
| 0x0407 | `home.max_travel_um` | u32 | um | 1000 | 500000 | 360000 | N |  |
| 0x0408 | `home.max_load_raw` | i32 | counts | 0 | 7151121 | 322123 | N |  |
| 0x0409 | `home.drift_tol_um` | u32 | um | 10 | 10000 | 200 | N |  |
| 0x0501 | `safety.load_raw_max` | i32 | counts | -7151120 | 7151121 | 7022271 | M | session value (not persisted) |
| 0x0502 | `safety.load_raw_min` | i32 | counts | -7151121 | 7151120 | -7022271 | M | session value (not persisted) |
| 0x0503 | `safety.load_trip_samples` | u8 | samples | 1 | 4 | 1 | MN |  |
| 0x0504 | `safety.load_regrow_raw` | i32 | counts | 0 | 1288490 | 128849 | MN |  |
| 0x0505 | `safety.zero_raw` | i32 | counts | -8388608 | 8388607 | 0 | M | session value (not persisted) |
| 0x0506 | `safety.release_band_raw` | i32 | counts | 0 | 644245 | 128849 | MN |  |
| 0x0507 | `safety.idle_disable_s` | u16 | s | 0 | 65535 | 600 | MN |  |
| 0x0508 | `safety.link_timeout_ms` | u16 | ms | 200 | 5000 | 1000 | MN |  |
| 0x0601 | `io.release_ms` | u8 | ms | 5 | 200 | 20 | N |  |
| 0x0602 | `io.estop_release_ms` | u16 | ms | 50 | 2000 | 100 | N |  |
| 0x0604 | `io.pause_active_level` | enum |  |  |  | CLOSED_ACTIVE | N | 0=OPEN_ACTIVE, 1=CLOSED_ACTIVE |
| 0x0701 | `drv.alm_active_level` | enum |  |  |  | HIGH_ACTIVE | N | 0=HIGH_ACTIVE, 1=LOW_ACTIVE |
| 0x0702 | `drv.pend_active_level` | enum |  |  |  | HIGH_ACTIVE | N | 0=HIGH_ACTIVE, 1=LOW_ACTIVE |
| 0x0703 | `drv.pend_timeout_ms` | u16 | ms | 0 | 5000 | 200 | MN |  |
| 0x0704 | `drv.pwr_sense_enable` | bool |  |  |  | false | NR |  |
| 0x0705 | `drv.k1_weld_ms` | u16 | ms | 100 | 2000 | 200 | N |  |
| 0x0706 | `drv.k1_check_enable` | bool |  |  |  | false | NR |  |
| 0x0801 | `stream.fallback_hz` | u8 | Hz | 1 | 80 | 10 | MN |  |

<!-- END GENERATED PARAM TABLE -->

---

## Appendix B. Protocol name registry (informative — generated; normative source: `protocol.yaml`)

<!-- BEGIN GENERATED protocol:registry (gen_protocol.py) -->

Generated from `protocol.yaml` — the single source of protocol names and codes (GF-08). C: `02_FW/src/gen/proto_gen.h`; Python: `03_SW/src/bend_stand/core/protocol_gen.py`. Bit identifiers: `<prefix><NAME>` = mask, `<prefix><NAME>_BIT` = index; Python `<ID>_BITS` = names by bit index, `<ID>_DESC` = descriptions.

| Table | Kind | ICD | C | Python |
|---|---|---|---|---|
| `commands` | enum | §3.2 | `CMD_*`, `CMD_REQ_LEN_*`, `CMD_IS_PRIORITY()`, `CMD_IS_SNIFFED()` | `Cmd`, `CMD_REQ_LEN`, `CMD_RETRY`, `CMD_PRIORITY`, `CMD_SNIFFED` |
| `constants` | — | B.1 | `PROTO_*` | module constants |
| `async_type` | enum | App. B | `ASYNC_*`, `proto_async_type_t` | `AsyncType` |
| `status_code` | enum | §4.2 | `ST_*`, `proto_status_code_t` | `Status` |
| `busy_detail` | enum | App. B | `BUSY_*`, `proto_busy_detail_t` | `BusyDetail` |
| `internal_detail` | enum | App. B | `INTERNAL_*`, `proto_internal_detail_t` | `InternalDetail` |
| `nvm_detail` | enum | App. B | `NVMD_*`, `proto_nvm_detail_t` | `NvmDetail` |
| `block` | bitset | §4.3 | `BLOCK_*` | `Block` |
| `stop_mode` | enum | App. B | `STOPMODE_*`, `proto_stop_mode_t` | `StopMode` |
| `mul_cmp` | enum | App. B | `CMP_*`, `proto_mul_cmp_t` | `MulCmp` |
| `home_flags` | bitset | App. B | `HOMEF_*` | `HomeFlags` |
| `motion_state` | enum | §6.1 | `MS_*`, `proto_motion_state_t` | `MotionState` |
| `home_phase` | enum | App. B | `HP_*`, `proto_home_phase_t` | `HomePhase` |
| `source` | enum | App. B | `SRC_*`, `proto_source_t` | `Source` |
| `reset_cause` | enum | App. B | `RST_*`, `proto_reset_cause_t` | `ResetCause` |
| `sys_flags` | bitset | §7.2 | `SYSF_*` | `SysFlags` |
| `features` | bitset | §7.1 | `FEAT_*` | `Features` |
| `data_flags` | bitset | §7.6 | `DF_*` | `DataFlags` |
| `data_status` | bitset | §7.6 | `DS_*` | `DataStatus` |
| `faults` | bitset | §7.6 | `FAULT_*` | `Faults` |
| `io` | bitset | §7.6 | `IO_*` | `IoBits` |
| `event` | events | §8.1 | `EV_*`, `proto_event_t` | `Event` |
| `stop_cause` | enum | §8.2 | `SC_*`, `proto_stop_cause_t` | `StopCause` |
| `move_done_reason` | enum | §8.3 | `MD_*`, `proto_move_done_reason_t` | `MoveDoneReason` |
| `home_fail_reason` | enum | App. B | `HF_*`, `proto_home_fail_reason_t` | `HomeFailReason` |
| `driver_disabled_cause` | enum | App. B | `DD_*`, `proto_driver_disabled_cause_t` | `DriverDisabledCause` |
| `pause_cleared_reason` | enum | App. B | `PCLR_*`, `proto_pause_cleared_reason_t` | `PauseClearedReason` |
| `params_defaulted_reason` | enum | App. B | `PDEF_*`, `proto_params_defaulted_reason_t` | `ParamsDefaultedReason` |
| `limit_id` | enum | App. B | `LIM_*`, `proto_limit_id_t` | `LimitId` |
| `afe_sample_status` | bitset | App. B | `AFES_*` | `AfeSampleStatus` |
| `meas_op` | enum | App. B | `MEAS_OP_*`, `proto_meas_op_t` | `MeasOp` |
| `meas_src` | enum | App. B | `MEAS_SRC_*`, `proto_meas_src_t` | `MeasSrc` |
| `meas_probe_mode` | enum | App. B | `MEAS_MODE_*`, `proto_meas_probe_mode_t` | `MeasProbeMode` |
| `meas_probe_flags` | bitset | App. B | `MEAS_PF_*` | `MeasProbeFlags` |
| `meas_chan` | enum | App. B | `MEAS_CHAN_*`, `proto_meas_chan_t` | `MeasChan` |
| `meas_hang_where` | enum | App. B | `MEAS_HANG_*`, `proto_meas_hang_where_t` | `MeasHangWhere` |
| `meas_pin` | enum | App. B | `MEAS_PIN_*`, `proto_meas_pin_t` | `MeasPin` |
| `meas_variant` | bitset | App. B | `MEAS_VAR_*` | `MeasVariant` |
| `retry_class` | enum | App. B | — | `RetryClass` |

### B.1 Constants

| Name | Value | Type | Meaning |
|---|---|---|---|
| `SYNC0` | 0xA5 | u8 | first sync byte (D-05 separator) |
| `SYNC1` | 0x5A | u8 | second sync byte |
| `HEADER_LEN` | 6 | u8 | SYNC0 SYNC1 TYPE SEQ LEN_lo LEN_hi |
| `FRAME_OVERHEAD` | 8 | u8 | header + CRC-16 |
| `MAX_LEN` | 160 | u16 | maximum payload length (frame <= 168 B) |
| `RX_BUF_MIN` | 336 | u16 | minimum receiver buffer (2 maximum frames, ICD §2.3) |
| `INTERBYTE_TIMEOUT_MS` | 20 | u16 | receiver inter-byte timeout |
| `CRC_INIT` | 0xFFFF | u16 | CRC-16/CCITT-FALSE init (poly 0x1021) |
| `CRC_POLY` | 0x1021 | u16 | CRC-16/CCITT-FALSE polynomial |
| `RESP_BIT` | 0x80 | u8 | response TYPE = command TYPE \| RESP_BIT |
| `NACK_LEN` | 3 | u8 | NACK payload: u8 status, u16 detail |
| `INFO_LEN` | 44 | u8 | INFO body (GET_INFO OK response after the STATUS byte) |
| `STATUS_LEN` | 86 | u8 | STATUS body (GET_STATUS OK response after the STATUS byte) |
| `DATA_LEN` | 18 | u8 | DATA payload, PAYLOAD_VERSION 1 |
| `EVENT_LEN` | 16 | u8 | EVENT payload |
| `PARAM_ENTRY_LEN` | 7 | u8 | u16 id, u8 type, u8[4] value |
| `PARAMS_PER_PAGE` | 20 | u8 | PARAM_ENTRYs per GET_ALL_PARAMS page |
| `REBOOT_MAGIC` | 0xB007B007 | u32 | REBOOT request magic |
| `JOG_NO_BOUND` | 0x80000000 (−2 147 483 648) | i32 | JOG bound_um = 0x80000000: no bound |
| `AFE_NO_DATA` | 0x80000000 (−2 147 483 648) | i32 | afe_raw / afe_raw_last = 0x80000000: no AFE sample |
| `RAW_MIN` | -8388608 | i32 | HX711 negative rail (saturated) |
| `RAW_MAX` | 8388607 | i32 | HX711 positive rail (saturated) |
| `DETAIL_CAUSE_INPUT` | 0xFFFF | u16 | E_CAUSE_ACTIVE detail of ESTOP_CLEAR / HALT_CLEAR: input still active |
| `TWIN_TCP_PORT` | 5760 | u16 | FW host twin serial-over-TCP port (127.0.0.1) |
| `TWIN_CTL_PORT` | 5761 | u16 | FW host twin world-control port (JSON lines, tools/README) |
| `HOME_RELEASE_MAX_UM` | 0x2710 | u32 | HOME: START not released within this travel in RELEASE / BACKOFF -> HOME_WIRING (ICD §5.4, OI-FW-23) |
| `MEAS_BODY_LEN` | 64 | u8 | DIAG_MEAS OK body: u32 w[16] (ICD Appendix C) |
| `MEAS_STAMPS_PER_PAGE` | 14 | u8 | DIAG_MEAS STAMPS: stamps per page (w2..w15) |
| `MEAS_MAGIC` | 0x4D454153 | u32 | DIAG_MEAS NOINIT w0 when the .noinit block is valid ('MEAS') |
| `HOME_SLOW_EXTRA_UM` | 0x2710 | u32 | HOME: no START edge within home.backoff_um + this travel in SLOW_APPROACH -> HOME_NOT_FOUND (ICD §5.4, OI-FW-23) |

### B.2 Asynchronous frame TYPEs (FW → PC)

Generated from `protocol.yaml` table `async_type` (C `ASYNC_*`, Python `AsyncType`).

| Code | Name | Meaning |
|---|---|---|
| `0xC0` | `DATA` | DATA frame (§7.3), one per HX711 conversion while the stream is on |
| `0xC1` | `EVENT` | EVENT frame (§7.4), sent regardless of the stream state |

### B.3 E_BUSY detail

Generated from `protocol.yaml` table `busy_detail` (C `BUSY_*`, Python `BusyDetail`).

| Code | Name | Meaning |
|---|---|---|
| 1 | `MOTION` | a motion is running (motion state MOVE_ABS, JOG, MOVE_UNTIL_LOAD, HOMING or STOPPING) |
| 2 | `ENABLING` | ENA settle (motion.ena_settle_ms) running |

### B.4 E_INTERNAL detail

Generated from `protocol.yaml` table `internal_detail` (C `INTERNAL_*`, Python `InternalDetail`).

| Code | Name | Meaning |
|---|---|---|
| 1 | `NOT_IN_BUILD` | command defined in the ICD but implemented in a later milestone (its feature bit is 0); nothing executed |
| 2 | `INVARIANT` | internal consistency check failed; nothing executed |

### B.5 E_NVM detail / NVM_ERROR arg

Generated from `protocol.yaml` table `nvm_detail` (C `NVMD_*`, Python `NvmDetail`).

| Code | Name | Meaning |
|---|---|---|
| 1 | `NO_RECORD` | no valid NVM record (LOAD_PARAMS), or a record that violates a hard rule |
| 2 | `ERASE_PROGRAM` | flash erase or program error |
| 3 | `VERIFY` | read-back verify error |

### B.6 STOP mode

Generated from `protocol.yaml` table `stop_mode` (C `STOPMODE_*`, Python `StopMode`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `IMMEDIATE` | no further PUL edge ≤ 2 ms after the last command byte (SAF-FW-002) |
| 1 | `CONTROLLED` | planned deceleration at motion.a_stop_um_s2 (clean halt only at step period > 2 ms and planned stop distance <= 1 step, §6.5) |

### B.7 MOVE_UNTIL_LOAD cmp

Generated from `protocol.yaml` table `mul_cmp` (C `CMP_*`, Python `MulCmp`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `GE` | stop when raw ≥ raw_stop |
| 1 | `LE` | stop when raw ≤ raw_stop |

### B.8 HOME flags

Generated from `protocol.yaml` table `home_flags` (C `HOMEF_*`, Python `HomeFlags`).

| Bit | Name | Meaning |
|---|---|---|
| 0 | `LOAD_CONFIRMED` | operator confirmed homing with load above home.max_load_raw (SAF-FW-021) |
| 1–7 | — | reserved (0) |

### B.9 Homing phase (STATUS home_phase)

Generated from `protocol.yaml` table `home_phase` (C `HP_*`, Python `HomePhase`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `NONE` | no homing since boot |
| 1 | `PRECHECK` | load pre-check |
| 2 | `RELEASE` | moving off an active START switch (+x) |
| 3 | `FAST_SEEK` | fast seek toward START (−x) at home.v_fast_um_s |
| 4 | `BACKOFF` | back-off home.backoff_um (+x) |
| 5 | `SLOW_APPROACH` | slow approach toward START at home.v_slow_um_s, edge capture |
| 6 | `MOVE_TO_ZERO` | move to x = 0 |
| 7 | `DONE` | last homing completed or failed |

### B.10 Latch source (STATUS halt_src / pause_src, EVENT HALT_SET / PAUSED arg)

Generated from `protocol.yaml` table `source` (C `SRC_*`, Python `Source`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `NONE` | not latched |
| 1 | `PC` | PC command (HALT, PAUSE) |
| 2 | `BUTTON` | physical PAUSE button (pause_src / PAUSED only; halt_src is never BUTTON since v0.5, D-36) |

### B.11 Reset cause (STATUS reset_cause, EVENT BOOT arg)

Generated from `protocol.yaml` table `reset_cause` (C `RST_*`, Python `ResetCause`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `UNKNOWN` | no flag recognised |
| 1 | `POWER_ON` | power-on / POR |
| 2 | `PIN` | NRST pin |
| 3 | `SOFTWARE` | software reset (REBOOT) |
| 4 | `IWDG` | independent watchdog |
| 5 | `WWDG` | window watchdog |
| 6 | `LOW_POWER` | low-power reset |
| 7 | `BROWN_OUT` | brown-out reset |

### B.12 HOME_FAILED reasons

Generated from `protocol.yaml` table `home_fail_reason` (C `HF_*`, Python `HomeFailReason`).

| Code | Name | Meaning |
|---|---|---|
| 1 | `NOT_FOUND` | no START edge within home.max_travel_um (fault HOME_NOT_FOUND) |
| 2 | `WIRING` | END switch reached (fault HOME_WIRING) |
| 3 | `ABORTED` | any other stop during homing (no latch of its own) |

### B.13 DRIVER_DISABLED causes

Generated from `protocol.yaml` table `driver_disabled_cause` (C `DD_*`, Python `DriverDisabledCause`).

| Code | Name | Meaning |
|---|---|---|
| 1 | `PC_DISABLE` | DISABLE command |
| 2 | `IDLE` | idle auto-disable (safety.idle_disable_s) |
| 3 | `ESTOP` | E-stop sense opened |
| 4 | `DRV_POWER_LOST` | driver power lost while enabled / enabling (SAF-FW-024, D-29c) |

### B.14 PAUSE_CLEARED reasons

Generated from `protocol.yaml` table `pause_cleared_reason` (C `PCLR_*`, Python `PauseClearedReason`).

| Code | Name | Meaning |
|---|---|---|
| 2 | `HALT_CLEAR` | accepted HALT_CLEAR (clears HALT and PAUSED, D-31) |
| 3 | `RESUME` | accepted RESUME (clears only PAUSED, D-31) |

### B.15 PARAMS_DEFAULTED reasons

Generated from `protocol.yaml` table `params_defaulted_reason` (C `PDEF_*`, Python `ParamsDefaultedReason`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `COMMAND` | DEFAULT_PARAMS |
| 1 | `NO_RECORD` | no NVM record (blank) |
| 2 | `CRC_ERROR` | both records CRC-bad |
| 3 | `MIGRATION` | record with another PARAM_DICT_HASH (migration by id) |
| 4 | `HARD_RULE` | resulting image violates a hard rule |

### B.16 Limit switch id (LIMIT_SET / LIMIT_CLEARED arg)

Generated from `protocol.yaml` table `limit_id` (C `LIM_*`, Python `LimitId`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `START` | START switch (−x end, home reference) |
| 1 | `END` | END switch (+x end) |

### B.17 afe_sample_t.status (seam hal_hx711, tools/README; not on the wire)

Generated from `protocol.yaml` table `afe_sample_status` (C `AFES_*`, Python `AfeSampleStatus`).

| Bit | Name | Meaning |
|---|---|---|
| 0 | `SCK_OVERRUN` | the read of this sample overran (SCK high > 60 us or DOUT still low after the last pulse): the HX711 may have entered power-down; the core re-initialises it (afe_reinit_count, EVENT AFE_REINIT) and flags the next afe.settle_discard samples AFE_SETTLING |
| 1 | `MISSED_EDGE` | at least one DOUT-ready edge was missed before this sample (recovered by hal_hx711_kick or a late edge): the core sets OVERRUN in the next DATA frame |
| 2–7 | — | reserved (0) |

### B.18 DIAG_MEAS op (request byte 0)

Generated from `protocol.yaml` table `meas_op` (C `MEAS_OP_*`, Python `MeasOp`).

| Code | Name | Meaning | `sel` | `a` | `b` | `retry` |
|---|---|---|---|---|---|---|
| 0 | `INFO` | variant, clocks, ring size, stamp overhead | 0 | 0 | 0 | RETRY |
| 1 | `PROBE_ARM` | arm the event-latency probe (MT-3) | meas_src | bits 0-1 meas_probe_mode, bit 8 event polarity (0 rising, 1 falling), other bits 0 | timer prescaler 0…65535 | VERIFY |
| 2 | `PROBE_READ` | read the probe captures | 0 | 0 | 0 | RETRY |
| 3 | `COUNTER` | independent PUL counter (MT-2): read / reset | 0 read, 1 reset (returns the value before the reset) | 0 | 0 | RETRY (read) / VERIFY (reset) |
| 4 | `STAMPS` | device-time stamp ring (MT-4), newest first | meas_chan | page 0…1023 | 0 | RETRY |
| 5 | `NOINIT` | .noinit block (last PUL, heartbeat, hang start, previous-boot record, boot counter; survives a reset) | 0 read, 1 clear | 0 | 0 | RETRY (read) / VERIFY (clear) |
| 6 | `STIM_RUN` | stimulus series on the J-STIM output (MT-7) | bit 0 polarity (0 high pulse, 1 low pulse), bits 1-7 hold time 1…127 ms | pulses 1…1000 | seed | VERIFY |
| 7 | `HANG` | test-image hang injection while moving (IWDG evidence) | meas_hang_where | duration 0…10000 ms (0 = until the IWDG resets) | 0 | VERIFY |
| 8 | `STATIC_LEVEL` | drive PUL or DIR statically for the DMM (only NOT_ENABLED; released before the next command is executed) | meas_pin | level 0/1 | 0 | VERIFY |
| 9 | `DWT` | DWT per-section cycle statistics (HW_MEAS_DWT builds; else w0 = 0) | 0 read, 1 reset | section 0…31 (0…22 defined, App. C table C.1) | 0 | RETRY (read) / VERIFY (reset) |

### B.19 DIAG_MEAS probe event source (PROBE_ARM sel; J-EVT selector position, FW_test_plan §6.2)

Generated from `protocol.yaml` table `meas_src` (C `MEAS_SRC_*`, Python `MeasSrc`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `ESTOP` | E-stop sense PA10 |
| 1 | `LIMIT_START` | START limit PB0 |
| 2 | `LIMIT_END` | END limit PC1 |
| 3 | `PAUSE` | PAUSE button PB6 |
| 4 | `DOUT` | HX711 DOUT PB4 (data ready) |
| 5 | `DRV_PWR` | driver-power sense PA7 |
| 6 | `RX` | USART2 RX PA3 (start bit of a frame byte) |
| 7 | `DIR` | DIR output node |
| 8 | `STIM` | stimulus output PB8 (self-test) |

### B.20 DIAG_MEAS probe mode (PROBE_ARM a bits 0-1)

Generated from `protocol.yaml` table `meas_probe_mode` (C `MEAS_MODE_*`, Python `MeasProbeMode`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `TRIGGER` | the first event edge after arming starts the probe counter (single shot) |
| 1 | `RESET` | every event edge restarts the probe counter (last event wins) |
| 2 | `PWM_INPUT` | PUL period and high width per pulse (min/max over the pulses since arming) |

### B.21 DIAG_MEAS PROBE_READ w0 flags

Generated from `protocol.yaml` table `meas_probe_flags` (C `MEAS_PF_*`, Python `MeasProbeFlags`).

| Bit | Name | Meaning |
|---|---|---|
| 0 | `ARMED` | probe armed |
| 1 | `TRIGGERED` | the event occurred (w1 valid) |
| 2 | `OVERCAPTURE` | a capture was overwritten before it was read (hardware over-capture) |
| 3 | `WINDOW_OVERFLOW` | the probe counter overflowed after the event (window 65 536 ticks): later captures invalid |
| 4 | `EDGE_BEFORE_EVENT` | a PUL edge was captured before the event (CCR = 0 case) |
| 5–15 | — | reserved (0) |

### B.22 DIAG_MEAS stamp channel (STAMPS sel)

Generated from `protocol.yaml` table `meas_chan` (C `MEAS_CHAN_*`, Python `MeasChan`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `EVT` | J-EVT edges (TIM8 CH1) |
| 1 | `PUL` | PUL rising edges (TIM8 CH2) |
| 2 | `DIR` | DIR edges (TIM8 CH4) |
| 3 | `AUX` | J-AUX edges (TIM1 CH4) |

### B.23 DIAG_MEAS HANG where (HANG sel)

Generated from `protocol.yaml` table `meas_hang_where` (C `MEAS_HANG_*`, Python `MeasHangWhere`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `MAIN` | main loop stops kicking the IWDG |
| 1 | `TICK` | the 1 kHz tick hangs |
| 2 | `ISR1` | level-1 ISR storm (software-triggered unused EXTI line, test image only) |

### B.24 DIAG_MEAS STATIC_LEVEL pin (sel)

Generated from `protocol.yaml` table `meas_pin` (C `MEAS_PIN_*`, Python `MeasPin`).

| Code | Name | Meaning |
|---|---|---|
| 0 | `PUL` | PUL output |
| 1 | `DIR` | DIR output |

### B.25 DIAG_MEAS INFO w0 variant

Generated from `protocol.yaml` table `meas_variant` (C `MEAS_VAR_*`, Python `MeasVariant`).

| Bit | Name | Meaning |
|---|---|---|
| 0 | `MEAS` | HW_MEAS build (MT-2/3/4/7, HANG, STATIC_LEVEL) |
| 1 | `DWT` | HW_MEAS_DWT build (DWT section statistics) |
| 2 | `TWIN_MODEL` | the FW host twin's model of DIAG_MEAS (REQ-C-M2-08) |
| 3–31 | — | reserved (0) |

### B.26 SW retry class (§9.3; not on the wire)

Generated from `protocol.yaml` table `retry_class` (Python `RetryClass` (not on the wire)).

| Code | Name | Meaning |
|---|---|---|
| 0 | `RETRY` | timeout 100 ms, ≤ 2 retries with a new SEQ and the newest value |
| 1 | `CONFIRM` | priority path, repeated every 50 ms until confirmed (≤ 20 in 1 s) |
| 2 | `ONCE_PRIORITY` | unused since ICD v0.4.1 (D-34: the clears are VERIFY); value kept, never reassigned |
| 3 | `VERIFY` | never retried; after a timeout GET_STATUS decides (commands with priority = true still use the SW priority lane) |

<!-- END GENERATED protocol:registry -->

---

## Appendix C. DIAG_MEAS ops and word layouts (D-40c; normative in HW_MEAS builds; confirmed by A, OI-FW-38; DMA map ASSUMED until HG-29)

Request: `u8 op` (table `meas_op`), `u8 sel`, `u16 a`, `u32 b` (ranges in the generated `meas_op` table, Appendix B).
OK body: `u32 w[16]`, little-endian; words not listed are 0. Times: `t_us` = the FW's 1 MHz device time (DATA / EVENT
domain); probe ticks = 180 MHz / (PSC + 1) of the 16-bit probe timer (TIM8). Sources, channels, modes and flag bits:
tables `meas_src`, `meas_chan`, `meas_probe_mode`, `meas_probe_flags`, `meas_variant`, `meas_hang_where`, `meas_pin`.

| op | Body words |
|---|---|
| 0 INFO | w0 variant (`meas_variant`), w1 probe timer clock Hz (180 000 000), w2 counter width bits (32, software-extended), w3 stamp clock Hz (1 000 000), w4 stamp ring size per channel (2048), w5 DMA stamp latency ns (≤ 1 000), w6 empty stamp-pair overhead cycles (calibrated at boot = section 21 of table C.1; 0 without DWT), w7 stimulus timer clock Hz (10 000 000). HW_MEAS_DWT builds: w0 = MEAS \| DWT |
| 1 PROBE_ARM | – (armed; previous captures discarded) |
| 2 PROBE_READ | w0 flags (`meas_probe_flags`; TRIGGERED = the probe counter is running), w1 CCR1 = 0 (the counter starts at the event), w2 CCR2 = last PUL rising edge after the event, w3 CCR3 = last ENA edge, w4 CCR4 = last DIR edge (probe ticks since the event), w5 PUL stamps since arming, w6 probe CNT now, w7 PSC, w8 / w9 PWM-input min / max PUL period, w10 / w11 min / max PUL high time, w12 PWM samples (probe ticks) |
| 3 COUNTER | w0 PUL rising edges since the last reset (32 bit), w1 `t_us` of the read (sel 1: values before the reset) |
| 4 STAMPS | w0 stamps written on the channel since boot / reset, w1 ring size, w2…w15 the 14 stamps of page `a`, newest first (stamp k = entry w0 − 1 − (14·a + k)); 0 = not available |
| 5 NOINIT | w0 magic `0x4D454153`, w1 last PUL `t_us` of this boot (0 until its first PUL), w2 heartbeat `t_us` (DMA-updated at 10 kHz: the last update, not the time of the read), w3 hang start `t_us` (HANG op; kept across resets until a clear), **w4 prev_valid** (1 = w5…w7 were snapshot at this boot from a valid block), **w5** previous boot's newest PUL `t_us` (0 = none), **w6** previous boot's last heartbeat `t_us` (≤ 100 µs before that boot ended), **w7** previous boot's hang start `t_us`, **w8** boot counter (boots since the block was initialised or cleared). At boot: block invalid → cleared (w1…w8 = 0) and the magic set; block valid → w4 = 1, w5…w7 snapshot, w8 + 1, the stamp rings cleared (each boot's rings hold only its own stamps). The block survives a reset (not a power cycle); sel 1 clears all words after reading (magic stays valid) |
| 6 STIM_RUN | – (series started: `a` pulses on the J-STIM output, each after a seeded random delay of 0…1 running step period (TIM2 ARR; 1 ms when the step timer is stopped), `sel` bits 1–7 = hold ms, bit 0 polarity) |
| 7 HANG | – (the selected context hangs for `a` ms, 0 = until the IWDG resets; only while moving) |
| 8 STATIC_LEVEL | – (PUL or DIR held at level `a` until the next command; only NOT_ENABLED and with the step timer stopped) |
| 9 DWT | statistics of **section `a`** (table C.1): w0 valid (1 in HW_MEAS_DWT builds, else 0), w1 count, w2 min cycles, w3 max cycles, w4 / w5 sum low / high (64-bit), w6…w15 histogram bins 0…9 of that section (cycles c at the 180 MHz core clock: bin 0 c < 512, bin k = 1…8 2^(k+8) ≤ c < 2^(k+9), bin 9 c ≥ 2^17); sections 23…31 read count 0; sel 1 resets the section after reading |

Retry class (§9.3): ops 0, 2, 4 and the read variants of 3, 5, 9 = RETRY; ops 1, 6, 7, 8 and the reset / clear
variants = VERIFY.

**Table C.1 — DWT sections (op 9 `a`; HW_MEAS_DWT image, A's `src/hal/f446/meas_dwt.h`, FW_design v0.6 §9.9).**
A section's figure = CYCCNT cycles between its entry and exit stamps; the record call runs after the exit stamp and
is not part of the section's own figure, but an outer section that contains an inner recorded section includes the
inner record call (section 22). Sections 21 / 22 are boot calibrations (16 samples each).

| a | Section | a | Section |
|---|---|---|---|
| 0 | main-loop pass (`app_loop()`) | 12 | CRIT_AFE window (BASEPRI 0x20) |
| 1 | TIM2 update ISR (step count + `step_isr`) | 13 | CRIT_MOTION window (BASEPRI 0x20) |
| 2 | EXTI15_10 E-stop handler | 14 | CRIT_DATA window (BASEPRI 0x30) |
| 3 | EXTI0 START limit handler | 15 | CRIT_TICK window (BASEPRI 0x40) |
| 4 | EXTI1 END limit handler | 16 | `hal_step_stop_now()` PRIMASK window |
| 5 | EXTI9_5 PAUSE handler | 17 | `hal_step_abort()` PRIMASK window |
| 6 | EXTI4 HX711 data-ready handler (read + `on_afe_sample`) | 18 | `hal_step_set_period_now()` PRIMASK window |
| 7 | TIM5 1 kHz tick (`core_tick_1ms`) | 19 | longest SCK-high of one HX711 read |
| 8 | USART2 error IRQ | 20 | one HX711 shift-in (24 + 1…3 bits) |
| 9 | DMA1 stream 5 (RX ring lap) | 21 | calibration: empty stamp pair (= INFO w6) |
| 10 | DMA1 stream 6 (TX complete → next frame) | 22 | calibration: one record call |
| 11 | CRIT_HALT window (PRIMASK) | 23…31 | reserved (31 = internal calibration scratch, reads 0) |
