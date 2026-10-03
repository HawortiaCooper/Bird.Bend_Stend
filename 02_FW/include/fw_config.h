/* FW constants that are not parameters (FW_design §2.5). Shared by core and the target HAL;
 * included by the host twin as well (plain C, no target headers).
 * Implements: FW-STR-002, IF-011, FW-NVM-001, NFR-005
 */
#ifndef FW_CONFIG_H
#define FW_CONFIG_H

#include "proto.h"

/* ---- link buffers (FW_design §5.9) ---- */
#define RX_RING_BYTES        2048u    /* DMA RX ring (ICD §9.1: >= 1 KB) */
#define RX_PARSE_BYTES       512u     /* frame parser buffer (ICD §2.3: >= 336) */
#define TX_R_BYTES           1024u    /* class R (responses) frame ring */
#define TX_E_SLOTS           16u      /* class E (EVENT) frames */
#define TX_D_SLOTS           2u       /* class D (DATA) mailbox frames */
/* one length byte per queued frame (pure txq) */
#define TX_E_BYTES           (TX_E_SLOTS * (PROTO_EVENT_FRAME_LEN + 1u))
#define TX_D_BYTES           (TX_D_SLOTS * (PROTO_DATA_FRAME_LEN + 1u))
/* the dispatcher takes a command only if class R can hold the largest response (DEF-P1-07);
 * hal_uart_tx_free() returns the largest frame a class accepts now */
#define TX_R_RESERVE         PROTO_FRAME_MAX

/* ---- main loop / link budgets ---- */
#define LINK_DISPATCH_BUDGET_US   500u   /* handler time per pass (FW_design §4.2) */
#define EVENTS_PER_PASS           4u     /* EVENT frames moved to class E per pass */
#define SNIFF_BYTES_PER_TICK      128u   /* > 92 B/ms line rate */
#define SNIFF_HOLD_MAX_MS         20u    /* = inter-byte timeout (FW_design §5.9.3) */
#define REBOOT_DRAIN_MAX_MS       40u    /* response drained, reset <= 50 ms after the response */

/* ---- NVM (FW_design §5.11): flash sectors 1 and 2, 512 B slots ---- */
#define NVM_SECTOR_A         1u
#define NVM_SECTOR_B         2u
#define NVM_ADDR_A           0x08004000UL
#define NVM_ADDR_B           0x08008000UL
#define NVM_SECTOR_BYTES     16384u
#define NVM_SAVE_WDG_MS      3000u    /* IWDG long window during erase/program (FW_design §5.13) */
#define NVM_QUIESCE_MAX_MS   100u     /* TX drain bound before programming (wire time <= 15 ms) */

/* ---- IWDG run window (FW_design §5.13): PR /8, RLR 190 -> 32.5..89.9 ms over the LSI range ---- */
#define WDG_RUN_TIMEOUT_MS   90u

/* ---- AFE ---- */
#define AFE_RATE_PERIODS     16u      /* median of 16 DOUT periods (FW-AFE-004) */

#endif /* FW_CONFIG_H */
