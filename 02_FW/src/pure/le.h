/* Origin: Thrust_Stand_HAW/02_FW/src/pure/le.h @37c8747 (copied unchanged, D-02/D-29m).
 */
/* Little-endian load/store helpers (ICD §0.1: all multi-byte fields little-endian).
 * Pure C11, host-testable.
 * Implements: IF-002 (byte order, ICD §0.1)
 */
#ifndef PURE_LE_H
#define PURE_LE_H

#include <stdint.h>
#include <string.h>

static inline void le_put16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)(v >> 8);
}

static inline void le_put32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)(v >> 24);
}

static inline uint16_t le_get16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | (uint16_t)((uint16_t)p[1] << 8));
}

static inline uint32_t le_get32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* IEEE-754 binary32 bit pattern of a float (no aliasing UB). */
static inline uint32_t f32_bits(float f)
{
    uint32_t u;
    memcpy(&u, &f, 4);
    return u;
}

static inline float f32_from_bits(uint32_t u)
{
    float f;
    memcpy(&f, &u, 4);
    return f;
}

#endif /* PURE_LE_H */
