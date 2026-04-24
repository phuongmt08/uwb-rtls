#SETUP GPS
## Đo Đạc
Đo khoảng cách từ mạch Tag tới Ground
Đo khoảng cách từ mạch Anchor tới Ground
Đo khoảng cách từ anchor 1 2 3 với hệ trục tọa độ đặt sẵn

## Setup firmware

Mở file positioning_config theo đường dẫn UWB-RTLS/firmware/uwb/sys/positioning_config.h
Nhập khoảng cách đo đạc 
/**
* @brief Tag height from ground (meters)
*/
#define TAG_HEIGHT_M            (0.148f)

/**
* @brief Anchor height from ground (meters)
*/
#define ANCHOR_HEIGHT_M         (0.415f)

Nhập vị trí anchor so với hệ trục tọa độ, Tag cần biết tọa độ của các anchor để tính Trilateration đúng
Nếu như thấy Error cao, cần check lại vị trí của các anchor

Nhập số lương của các anchor setup, lưu ý nếu như nhập sai thì tọa độ sẽ không được tính

#define NUM_ANCHORS  3  /* Total anchors */

#define ANCHOR_1_X   0.0f
#define ANCHOR_1_Y   0.0f

#define ANCHOR_2_X   9.76f
#define ANCHOR_2_Y   0.0f

#define ANCHOR_3_X   4.88f
#define ANCHOR_3_Y   14.64f

#define ANCHOR_4_X   0.0f
#define ANCHOR_4_Y   0.0f

Một số lưu ý sau

Nếu như thấy tỉ lệ mất gói cao -> 