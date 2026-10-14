# Flashing the Bird Bend Stand firmware 0.8.6 onto the NUCLEO-F446RE

| | |
|---|---|
| Package | `BringUp_FW0.8.6/firmware/` — release image `BBS_FW_0.8.6_release.bin` / `.hex` |
| Build | FW **0.8.6**, GET_INFO build string **`20261010-2135`** (= time of the source commit, UTC — reproducible build), dictionary hash **`0xF8BCDCB8`**, ICD 0.7.5 (`BUILD_INFO.txt`) |
| Who | the product owner, or the technician the PO has released this board to (D-06: nobody flashes a board or opens its COM port without the PO's go) |
| Back to the guide | [BRINGUP_GUIDE](../BRINGUP_GUIDE.md) step 5 |

## 1. Which file and which method — read this first

The board keeps its **saved configuration** (board parameters such as the travel calibration result
`motion.steps_per_mm`, `motion.dir_invert`, ALM polarity, …) in **flash sectors 1 and 2** (`0x08004000…0x0800BFFF`,
the NVM log). The firmware itself lives in sector 0 (vector table) and from sector 3 on (`0x0800C000…`).

| Method | File | Saved configuration (sectors 1–2) | Use it for |
|---|---|---|---|
| **A. ST-LINK drag-and-drop** onto the Nucleo USB drive | `.bin` | **WIPED.** The `.bin` is one block from `0x08000000` to the end of the code, so it covers sectors 1–2 (they are written with `0xFF` = erased). The ST-LINK drive programming erases every sector it writes; depending on the ST-LINK firmware version it may even erase the whole chip first. | **First bring-up** (simplest; nothing to keep yet — the board still holds Stefan's old program) |
| **B1. STM32CubeProgrammer, full chip erase + `.hex`** | `.hex` | **WIPED** (on purpose) | first bring-up, if drag-and-drop is not available, or to start from a clean board |
| **B2. STM32CubeProgrammer, `.hex`, NO full chip erase** | `.hex` | **KEPT.** The `.hex` contains only the vector table (sector 0) and the code (`0x0800C000…0x080178DF`, sectors 3–4); the programmer erases only the sectors it writes — **sectors 1–2 are not touched** (checked at packaging: no HEX record in `0x08004000…0x0800BFFF`). The values are kept as long as the new firmware has the **same dictionary hash** (`0xF8BCDCB8`); after a dictionary change they are migrated by parameter id and **CFG** shows `NVM defaulted` — check them, then **Save to NVM**. | **every later update** of a configured board |

Rules:
- **Never use the `.bin` for an update of a configured board** — any `.bin` that starts at `0x08000000` wipes the
  saved configuration, with any tool.
- Before **any** update, save the board configuration on the PC: Bird Bend Stand → **Connection & Config** →
  **Save to file…** (`*.bbboard.json`). After an accidental wipe: **Load from file…** → **Write & verify** →
  **Save to NVM** restores it.
- After a wipe the board boots on its **default parameters**: the PC application shows the notice
  `Board runs on default parameters (no valid NVM record) – check and Save to NVM.` and the **CFG** chip
  `NVM defaulted`. That is expected after method A / B1.
- This package contains **only the release image**. The measurement images for the hardware gate are in a separate kit
  (`04_Delivery/GateKit_FW0.8.6/`), used only in a gate session with Validator E — never for normal operation.
- The driver may stay unpowered during flashing; if it is powered, the motor keeps holding while the Nucleo is
  reset (D-13) and the firmware starts with pulses blocked and the axis NOT homed.

## 2. Method A — drag-and-drop (first bring-up)

1. Close the Bird Bend Stand application (it must not hold the COM port). Plug the Nucleo into the PC by USB
   (USB connector of the ST-LINK part, top of the board).
2. Windows shows a USB drive named **`NODE_F446RE`** (the exact name depends on the ST-LINK firmware; any drive
   that appears when the Nucleo is plugged in and contains `MBED.HTM` / `DETAILS.TXT` is the ST-LINK drive).
3. Check the file first (section 5, hash). Copy **`BBS_FW_0.8.6_release.bin`** onto that drive
   (drag and drop in Explorer, or `copy BBS_FW_0.8.6_release.bin E:\` with the drive letter of the Nucleo).
4. The COM LED **LD1** next to the USB connector flickers while the ST-LINK programs; the drive disappears and comes
   back after a few seconds. **Do not unplug during this.**
5. Open the drive again: if a file **`FAIL.TXT`** is there, programming failed → read it, unplug / replug the USB and
   copy again; if it fails twice, stop and use method B1.
6. The board starts the new firmware by itself (if not: press the black **RESET** button B2 once). The green LED
   **LD2** (2 s pattern, `led.c`): **slow blink** (1 s on / 1 s off) = running, no latch — also with one limit switch
   open or unplugged; **double blink** = a latch is active (HALT, PAUSED, ESTOP or a FAULT, e.g. the red E-stop
   pressed or `LIMIT_WIRING` with both limit connectors open); **triple blink** = the board runs on its fallback
   clock → **STOP** (solder bridges SB16 / SB50, 8 MHz from the ST-LINK). LD2 dark or steady → **STOP**.
7. Verify (section 5).

## 3. Method B — STM32CubeProgrammer (GUI)

STM32CubeProgrammer (free from st.com; also included in STM32CubeCLT). Connect only the Nucleo USB.

1. Close the Bird Bend Stand application. Start STM32CubeProgrammer.
2. Right side, **ST-LINK** configuration: Port **SWD**, Mode **Under reset**, Reset mode **Hardware reset** →
   **Connect**. The log shows `Device name : STM32F446xx`.
3. **Backup first** (for B2): Bird Bend Stand **Save to file…** was done before (section 1). Optional extra copy of
   the raw NVM sectors: in the *Memory & File editing* view read address `0x08004000`, size `0x8000`, and
   **Save As…** `nvm_backup_<date>.bin` (read only, changes nothing).
4. Left toolbar → **Erasing & Programming**:
   - **B1 (first bring-up / clean board):** press **Full chip erase**, confirm, wait for "Mass erase successfully
     achieved".
   - **B2 (update, keep the configuration):** do **not** press Full chip erase; leave
     *Skip flash erase before programming* **unticked** (the programmer must erase sectors 0, 3 and 4 itself).
   - **File path:** `BBS_FW_0.8.6_release.hex` (no start address needed for a `.hex`).
   - Tick **Verify programming** and **Run after programming** → **Start Programming**.
   - Expected: "File download complete", "Download verified successfully".
5. **Disconnect**, then verify (section 5).

## 4. Method B — STM32CubeProgrammer CLI

From the folder `BringUp_FW0.8.6\firmware` (`STM32_Programmer_CLI.exe` is in
`…\STM32CubeProgrammer\bin` or `…\STM32CubeCLT_1.20.0\STM32CubeProgrammer\bin`; options as documented by the
CLI help of STM32CubeProgrammer v2.21 — not yet run against this board, D-06):

```bat
rem B1 - first bring-up / clean board: full chip erase, program, verify, reset (WIPES the saved configuration)
STM32_Programmer_CLI -c port=SWD mode=UR -e all -w BBS_FW_0.8.6_release.hex -v -rst

rem B2 - update of a configured board: erases only sectors 0, 3, 4 (KEEPS sectors 1-2 = saved configuration)
STM32_Programmer_CLI -c port=SWD mode=UR -w BBS_FW_0.8.6_release.hex -v -rst

rem optional before B2: raw copy of the NVM sectors 1-2 (read only)
STM32_Programmer_CLI -c port=SWD mode=UR -u 0x08004000 0x8000 nvm_backup_20261014.bin
```

Never add `-e all` and never use the `.bin` for B2. The HW-gate runbook
(`00_System/tools/hil/HW_GATE_RUNBOOK.md` §2.4) uses the same B2 command with the images of the gate kit
`04_Delivery/GateKit_FW0.8.6/`.

## 5. Verify the flashed image

1. **File hash before flashing** (PowerShell in the `firmware` folder):
   `Get-FileHash BBS_FW_0.8.6_release.hex -Algorithm SHA256` (or `certutil -hashfile BBS_FW_0.8.6_release.hex SHA256`)
   — the value must equal the line in `SHA256SUMS.txt`:

   | File | SHA-256 |
   |---|---|
   | `BBS_FW_0.8.6_release.bin` | `5e5a238683d59864399d08b6743a440d4a6088d024f1dc9938357f03d70034b2` |
   | `BBS_FW_0.8.6_release.hex` | `30989ec53529fd252a20c5ff7e5a4bf780d126b94ca919439eb282502ebddbc0` |

2. **Programmer verify:** method B prints "Download verified successfully"; method A has no `FAIL.TXT`.
3. **Board identity in the PC application** (Bird Bend Stand → **Connection & Config** → **Connect**): the
   **Device** line must read

   `Device: FW 0.8.6 (build 20261010-2135)  proto 1.0  payload 1  dict hash 0xF8BCDCB8 ✓  UID <24 hex digits>  features AFE BUTTONS DRV_SIGNALS HOMING MOTION MOVE_UNTIL_LOAD NVM`

   and the next line `Version check: ✓ compatible`. Notes:
   - The build string `20261010-2135` is the time of the source commit (UTC), not the flashing day: every
     rebuild of this firmware gives the same string and the same file hashes (`BUILD_INFO.txt`).
   - `HW_MEAS` must **not** be in the feature list (it is only in the gate-kit images, build `…-MEAS` / `…-DWT`).
   - Write the **UID** into the bring-up record (it identifies the board for the HW gate, D-06-GATE).
   - **Help ▸ About…** shows the same FW, protocol and dictionary versions.
4. **Configuration:** after method A / B1 the notice "Board runs on default parameters …" and **CFG**
   `NVM defaulted` (possibly `dirty NVM defaulted`) are expected (configure, then **Save to NVM**). After method
   B2 with an unchanged dictionary hash the **CFG** chip shows `clean`; after a dictionary change the values are
   migrated by id and **CFG** shows `NVM defaulted` → check them, then **Save to NVM**. With `clean`
   the parameters are the ones saved before the update.

## 6. If something goes wrong

| Symptom | What to do |
|---|---|
| No `NODE_F446RE` drive, no ST-LINK in CubeProgrammer | other USB cable (data cable) / port; Nucleo jumper CN2 (ST-LINK) both fitted; ST-LINK driver (STSW-LINK009) installed |
| `FAIL.TXT` on the drive | replug and copy again once; else method B1 |
| CubeProgrammer cannot connect | Mode **Under reset**; the Bird Bend Stand app closed; press and hold RESET B2 while pressing Connect, release after "connecting" |
| Device line shows another build / dict hash | wrong file or the flash failed → repeat with the right file, check the hash |
| `Version check: ✗ …` / **RO** chip | the PC software does not match this firmware — use the PC software of this package |
| Configuration lost after an update | it was flashed with method A / B1 or with the `.bin`: **Load from file…** → **Write & verify** → **Save to NVM** |
