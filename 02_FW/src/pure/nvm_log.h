/* NVM record log (FW_design §5.11, ICD §11.2/§11.3): record codec, two-sector slot log, boot and
 * LOAD rules 2..5, CFG_DIRTY comparison. Operates on byte images of the two sectors (target:
 * memory-mapped flash via hal_flash_map). Pure C11.
 *
 * Record (TS nvm_codec format): 32-byte header
 *   0 u32 magic | 4 u16 layout | 6 u16 header size | 8 u32 seq | 12 u16 entry count |
 *   14 u16 entry size | 16 u32 PARAM_DICT_HASH | 20 u16 dict_version | 22 u16 fw_version |
 *   24 u32 CRC-32 of the entries | 28 u32 CRC-32 of bytes 0..27 (written LAST = commit marker)
 * then entry_count x 8-byte entries {u16 id, u8 type, u8 0, u32 raw} of every `nvm` parameter in
 * ascending id order. One record per 512-byte slot, 32 slots per 16 KB sector.
 * Log rule: append to the next free slot of the sector holding the newest record; when that
 * sector is full, erase the other one and continue there (a power loss at any word or during the
 * erase leaves the previous record valid). Newest = largest seq (modular).
 *
 * Origin: Thrust_Stand_HAW/02_FW/src/pure/nvm_codec.* @37c8747 (codec adapted: slot log instead
 * of two fixed banks, no fw_owned parameters, boot/LOAD rule functions added).
 * Implements: FW-NVM-001, FW-NVM-002
 */
#ifndef PURE_NVM_LOG_H
#define PURE_NVM_LOG_H

#include <stdbool.h>
#include <stdint.h>

#include "params_gen.h"
#include "proto.h"

#ifdef __cplusplus
extern "C" {
#endif

#define NVM_MAGIC            0x564E4442u   /* "BDNV" little-endian bytes 42 44 4E 56 */
#define NVM_LAYOUT_VERSION   1u
#define NVM_HDR_SIZE         32u
#define NVM_ENTRY_SIZE       8u
#define NVM_SLOT_SIZE        512u
#define NVM_LOG_SECTOR_BYTES 16384u
#define NVM_SLOTS            (NVM_LOG_SECTOR_BYTES / NVM_SLOT_SIZE)   /* 32 */
#define NVM_MAX_ENTRIES      ((NVM_SLOT_SIZE - NVM_HDR_SIZE) / NVM_ENTRY_SIZE)
PROTO_STATIC_ASSERT(NVM_HDR_SIZE + NVM_ENTRY_SIZE * PARAM_COUNT <= NVM_SLOT_SIZE, "record fits a slot");

typedef enum { NVM_SLOT_BLANK = 0, NVM_SLOT_INVALID = 1, NVM_SLOT_VALID = 2 } nvm_slot_state_t;

typedef struct {
    uint32_t seq;
    uint16_t entry_count;
    uint32_t dict_hash;
    uint16_t dict_version;
    uint16_t fw_version;
} nvm_hdr_t;

typedef struct {
    bool     found;          /* a valid record exists */
    uint8_t  sector;         /* 0 = A, 1 = B */
    uint16_t slot;
    nvm_hdr_t hdr;
    bool     any_nonblank;   /* some slot is not blank (-> PDEF_CRC_ERROR when nothing is valid) */
} nvm_newest_t;

typedef struct {
    uint8_t  sector;         /* 0 = A, 1 = B */
    uint16_t slot;
    bool     erase_first;    /* the target sector must be erased before programming */
    uint32_t seq;            /* sequence number of the new record */
} nvm_target_t;

typedef struct {
    uint16_t event;          /* EV_PARAMS_LOADED or EV_PARAMS_DEFAULTED */
    uint16_t arg;            /* PARAMS_LOADED: values replaced; PARAMS_DEFAULTED: PDEF_* */
    bool     nvm_defaulted;  /* sys_flags NVM_DEFAULTED (rules 2/4/5) */
} nvm_result_t;

/** Check one slot image (NVM_SLOT_SIZE bytes); *hdr filled when valid. Blank = all 0xFF. */
nvm_slot_state_t nvm_slot_check(const uint8_t *slot, nvm_hdr_t *hdr);
/** Scan both sector images for the newest valid record. */
void nvm_scan(const uint8_t *sec_a, const uint8_t *sec_b, nvm_newest_t *out);
/** Where the next record goes (log rule above). */
void nvm_next_target(const uint8_t *sec_a, const uint8_t *sec_b, const nvm_newest_t *newest,
                     nvm_target_t *out);
/** Number of `nvm` parameters (entries per record). */
uint16_t nvm_entry_count(void);
/** Build a record image of the `nvm` parameters of *p into out (>= NVM_SLOT_SIZE bytes); returns
 *  the record size. */
uint16_t nvm_build(const params_t *p, uint32_t seq, uint16_t fw_version, uint8_t *out);
/** CFG_DIRTY = 0 iff the record has the FW hash and every `nvm` parameter equals it (ICD §11.2). */
bool nvm_matches(const uint8_t *rec, const nvm_hdr_t *hdr, const params_t *p);

/** Boot rules 2..5 (ICD §11.3): rec = NULL when no valid record; *out = resulting image
 *  (session parameters at their defaults). */
void nvm_apply_boot(const uint8_t *rec, const nvm_hdr_t *hdr, bool any_nonblank, params_t *out,
                    nvm_result_t *res);
/** LOAD_PARAMS rules 3..5 on RAM: false (RAM unchanged) if the result violates a hard rule
 *  (E_NVM NVMD_NO_RECORD); session parameters keep their RAM values. */
bool nvm_apply_load(const uint8_t *rec, const nvm_hdr_t *hdr, params_t *ram, nvm_result_t *res);

#ifdef __cplusplus
}
#endif

#endif /* PURE_NVM_LOG_H */
