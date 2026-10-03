/* Build id for GET_INFO (ICD §7.1 `build`, 16 B): FW_BUILD_DATE from tools/build_info.py
 * ("YYYYMMDD-HHMM", measurement images "YYMMDD-HHMM-MEAS" / "-DWT"), "host" in host builds. Kept in its
 * own object so that every other core object is byte-identical between the release and the
 * measurement images (TC-SYS-009-02, CR-02).
 * Implements: FW-CFG-004
 */
#if defined(__has_include)
#if __has_include("build_info_gen.h")
#include "build_info_gen.h"
#endif
#endif
#ifndef FW_BUILD_DATE
#define FW_BUILD_DATE "host"
#endif

extern const char fw_build_id[];
const char fw_build_id[] = FW_BUILD_DATE;
