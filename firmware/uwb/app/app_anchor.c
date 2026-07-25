/**
 * @file       app_anchor.c
 * @brief      Normal non-blocking anchor ranging application
 */

#include "app_anchor.h"

#include "sys_config.h"
#include "sys_logger.h"
#include "sys_ranging.h"

static uint8_t anchor_fill_ids(uint8_t *anchor_ids)
{
  sys_config_t *cfg = sys_config_get();
  uint32_t count = cfg->anchor_count;

  if (!anchor_ids || count == 0U || count > MAX_ANCHORS_SUPPORTED)
  {
    return 0U;
  }

  for (uint32_t i = 0U; i < count; i++)
  {
    anchor_ids[i] = (uint8_t)cfg->anchor_layout[i].anchor_id;
  }
  return (uint8_t)count;
}

app_err_t app_anchor_init(void)
{
  const sys_config_t *cfg = sys_config_get();
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "===== ANCHOR #%u =====", cfg->uwb.device_id);
  return APP_OK;
}

void app_anchor_process(void *arg)
{
  (void)arg;

  sys_config_t *cfg = sys_config_get();
  uint8_t anchor_ids[MAX_ANCHORS_SUPPORTED];
  uint8_t anchor_count = anchor_fill_ids(anchor_ids);
  if (anchor_count == 0U)
  {
    return;
  }

  sys_ranging_err_t err =
      sys_ranging_anchor_process_tdma(anchor_count, anchor_ids, cfg->uwb.rx_timeout_ms);
  if (err != SYS_RANGING_OK)
  {
    return;
  }

  sys_ranging_result_t result;
  if (sys_ranging_anchor_get_result_tdma(&result) == SYS_RANGING_OK && result.valid)
  {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR,
           "[DIST] Anchor #%u: tag_distance=%.3fm",
           cfg->uwb.device_id,
           result.distance_m);
  }
}
