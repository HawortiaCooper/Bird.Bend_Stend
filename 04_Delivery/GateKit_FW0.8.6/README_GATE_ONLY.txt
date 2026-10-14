ONLY FOR THE HW GATE WITH VALIDATOR E - NEVER FOR NORMAL OPERATION

These measurement images (HW_MEAS, HW_MEAS_DWT) contain the diagnostic command DIAG_MEAS (0x3D):
stimulus output on PB8, timer captures on the measurement header, HANG injection, static output levels.
They are flashed by the product owner during a D-06-GATE session only, when the session runner asks
for them (00_System/tools/hil/HW_GATE_RUNBOOK.md). After the gate the release image
(../BringUp_FW0.8.6/firmware/BBS_FW_0.8.6_release.hex of the operator package) is flashed
again (IMG-REL / HG-30). Hashes: SHA256SUMS.txt here; build identity: BUILD_INFO.txt here.
GET_INFO build string ends in -MEAS / -DWT and the feature list contains HW_MEAS.
