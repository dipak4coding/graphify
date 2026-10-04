#ifndef KKU_H
#define KKU_H
typedef unsigned char T_U8;
typedef short T_S16;

typedef struct {
    T_S16 SW_T_Oel;       /* scalar calibration */
    T_U8  Kku_Flags;      /* bit-flag calibration */
    T_S16 Ax_X[2];       /* axis breakpoints */
    T_S16 Kku_Tab_kl[2]; /* curve data */
} CAL_APP;

extern const CAL_APP *KkuAppPtr;
extern T_S16 Kku_T_Oel;
extern T_U8  Kku_Anf;
extern T_S16 Kku_Out;

void  Bios_RegisterCalibrationData(T_U8 **p, const T_U8 *rom, T_U8 n);
T_S16 Ipo_2D_S8(const T_S16 *x_axis, const T_S16 *curve, T_S16 in, T_U8 len);

void  Kku_Init(void);
T_U8  Kku_BerKuehlAnf(void);
void  Kku_Task10ms(void);
#endif
