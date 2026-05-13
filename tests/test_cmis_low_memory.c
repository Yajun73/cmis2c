#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "cmis_low_memory.h"

int main(void) {
    cmis_low_memory_t regs;
    int failures = 0;

    /* Zero-init to avoid warnings about uninitialized reads */
    uint8_t *bytes = (uint8_t *)&regs;
    for (size_t i = 0; i < sizeof(regs); i++) {
        bytes[i] = 0;
    }

    /* ---------------------------------------------------------------
     * 1. Size check (also enforced at compile time by _Static_assert)
     * --------------------------------------------------------------- */
    printf("sizeof(cmis_low_memory_t) = %zu (expected 128)\n", sizeof(regs));
    if (sizeof(regs) != 128) {
        printf("FAIL: wrong struct size\n");
        failures++;
    }

    /* ---------------------------------------------------------------
     * 2. Byte-level offset checks against CMIS spec
     * --------------------------------------------------------------- */
    #define CHECK_OFFSET(field, expected_byte) do {                           \
        ptrdiff_t off = (uint8_t *)&regs.field - (uint8_t *)&regs;          \
        if (off != (expected_byte)) {                                        \
            printf("FAIL: %s at offset %td, expected %d\n",                  \
                   #field, off, (expected_byte));                            \
            failures++;                                                      \
        } else {                                                             \
            printf("OK:   %s at offset %d\n", #field, (expected_byte));      \
        }                                                                    \
    } while (0)

    CHECK_OFFSET(SFF8024Identifier, 0);
    CHECK_OFFSET(CmisRevision, 1);

    /* Byte 2: union with bit-fields */
    regs.r2 = 0xFF;
    regs.MemoryModel = 0;
    if (regs.MemoryModel != 0) { printf("FAIL: MemoryModel bit-field write/read\n"); failures++; }

    CHECK_OFFSET(r2, 2);

    /* Byte 3: ModuleState union */
    regs.r3 = 0xFF;
    regs.InterruptDeasserted = 0;
    if (regs.InterruptDeasserted != 0) { printf("FAIL: InterruptDeasserted bit-field\n"); failures++; }

    CHECK_OFFSET(r3, 3);

    /* Monitor values (uint16_t, bytes 14-25) */
    CHECK_OFFSET(TempMonValue, 14);
    CHECK_OFFSET(VccMonVoltage, 16);
    CHECK_OFFSET(Aux1MonValue, 18);
    CHECK_OFFSET(Aux2MonValue, 20);
    CHECK_OFFSET(Aux3MonValue, 22);
    CHECK_OFFSET(CustomMonValue, 24);

    /* CDB status (bytes 37-38) */
    CHECK_OFFSET(CdbStatus1, 37);
    CHECK_OFFSET(CdbStatus2, 38);

    /* Firmware revision (bytes 39-40) */
    CHECK_OFFSET(ModuleActiveFirmwareMajorRevision, 39);
    CHECK_OFFSET(ModuleActiveFirmwareMinorRevision, 40);

    /* Password areas (uint32_t, bytes 118-125) */
    CHECK_OFFSET(PasswordChangeEntryArea, 118);
    CHECK_OFFSET(PasswordEntryArea, 122);

    /* Page/Bank select (bytes 126-127) */
    CHECK_OFFSET(BankSelect, 126);
    CHECK_OFFSET(PageSelect, 127);

    /* ---------------------------------------------------------------
     * 3. AppDescriptors array access
     * --------------------------------------------------------------- */
    regs.AppDescriptors[0].HostInterfaceID = 0xAB;
    regs.AppDescriptors[0].MediaInterfaceID = 0xCD;
    if (regs.AppDescriptors[0].HostInterfaceID != 0xAB) {
        printf("FAIL: AppDescriptors[0].HostInterfaceID\n");
        failures++;
    } else {
        printf("OK:   AppDescriptors[0] readback works\n");
    }

    regs.AppDescriptors[7].HostLaneCount = 3;
    regs.AppDescriptors[7].MediaLaneCount = 4;
    if (regs.AppDescriptors[7].HostLaneCount != 3 || regs.AppDescriptors[7].MediaLaneCount != 4) {
        printf("FAIL: AppDescriptors[7] lane counts\n");
        failures++;
    } else {
        printf("OK:   AppDescriptors[7] lane counts work\n");
    }

    /* ---------------------------------------------------------------
     * 4. Enums
     * --------------------------------------------------------------- */
    Enums_ModuleState s = Enums_ModuleState_ModuleReady;
    if (s != 3) {
        printf("FAIL: Enums_ModuleState_ModuleReady != 3\n");
        failures++;
    } else {
        printf("OK:   Enums_ModuleState = ModuleReady (3)\n");
    }

    /* ---------------------------------------------------------------
     * 5. Result
     * --------------------------------------------------------------- */
    if (failures == 0) {
        printf("\nAll checks passed.\n");
    } else {
        printf("\n%d check(s) FAILED.\n", failures);
    }

    return failures;
}
