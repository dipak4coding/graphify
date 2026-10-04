#include "kku.h"

static const CAL_APP KkuAppROM = {0};
const CAL_APP *KkuAppPtr;

T_S16 Kku_T_Oel;
T_U8  Kku_Anf;
T_S16 Kku_Out;

void Kku_Init(void)
{
    Bios_RegisterCalibrationData((T_U8 **)&KkuAppPtr, (const T_U8 *)&KkuAppROM, sizeof(KkuAppROM));
}

T_U8 Kku_BerKuehlAnf(void)
{
    if (Kku_T_Oel > KkuAppPtr->SW_T_Oel) {
        Kku_Anf = 1;
    } else {
        Kku_Anf = 0;
    }
    if (KkuAppPtr->Kku_Flags & (1 << 1)) {
        Kku_Anf += 1;
    }
    return Kku_Anf;
}

void Kku_Task10ms(void)
{
    T_U8 i;
    T_S16 sum = 0;
    for (i = 0; i < 2; i++) {
        sum += KkuAppPtr->Kku_Tab_kl[i];
    }
    Kku_Out = Ipo_2D_S8(KkuAppPtr->Ax_X, KkuAppPtr->Kku_Tab_kl, Kku_T_Oel, 2) + sum;
    if (Kku_BerKuehlAnf()) {
        Kku_Out += 1;
    }
}
