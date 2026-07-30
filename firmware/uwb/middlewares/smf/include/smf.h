/*
 * Portable adaptation of the Zephyr State Machine Framework.
 * This file has been modified for use as a standalone project middleware.
 *
 * Copyright 2021 The Chromium OS Authors
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef SMF_H
#define SMF_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C"
{
#endif

#ifndef SMF_INSTRUMENTATION
#define SMF_INSTRUMENTATION 0
#endif

  typedef enum
  {
    SMF_EVENT_HANDLED = 0,
    SMF_EVENT_PROPAGATE
  } smf_state_result_t;

  typedef enum
  {
    SMF_ACTION_ENTRY = 0,
    SMF_ACTION_RUN,
    SMF_ACTION_EXIT
  } smf_action_type_t;

  typedef void (*smf_action_t)(void *obj);
  typedef smf_state_result_t (*smf_run_t)(void *obj);

  typedef struct smf_state smf_state_t;
  typedef struct smf_ctx   smf_ctx_t;

  struct smf_state
  {
    smf_action_t       entry;
    smf_run_t          run;
    smf_action_t       exit;
    const smf_state_t *parent;
    const smf_state_t *initial;
  };

#if SMF_INSTRUMENTATION
  typedef void (*smf_transition_hook_t)(smf_ctx_t         *ctx,
                                        const smf_state_t *source,
                                        const smf_state_t *destination);
  typedef void (*smf_action_hook_t)(smf_ctx_t         *ctx,
                                    const smf_state_t *state,
                                    smf_action_type_t  action);
  typedef void (*smf_error_hook_t)(smf_ctx_t *ctx, int error_code);

  typedef struct
  {
    smf_transition_hook_t on_transition;
    smf_action_hook_t     on_action;
    smf_error_hook_t      on_error;
  } smf_hooks_t;
#endif

  struct smf_ctx
  {
    const smf_state_t *current;
    const smf_state_t *previous;
    const smf_state_t *executing;
    int32_t            terminate_value;
    bool               new_state;
    bool               terminate;
    bool               in_exit;
    bool               handled;
#if SMF_INSTRUMENTATION
    const smf_hooks_t *hooks;
#endif
  };

#define SMF_STATE(_entry, _run, _exit, _parent, _initial) \
  {                                                       \
    .entry   = (_entry),                                  \
    .run     = (_run),                                    \
    .exit    = (_exit),                                   \
    .parent  = (_parent),                                 \
    .initial = (_initial),                                \
  }

#define SMF_REF(_table, _state) (&(_table)[(_state)])

/*
 * The state-machine context must be the first member of the application object.
 */
#define SMF_CTX(_obj) ((smf_ctx_t *) (_obj))

  enum
  {
    SMF_ERR_NULL_TRANSITION = 1,
    SMF_ERR_TRANSITION_IN_EXIT
  };

  void    smf_set_initial(smf_ctx_t *ctx, const smf_state_t *initial_state);
  void    smf_set_state(smf_ctx_t *ctx, const smf_state_t *new_state);
  void    smf_set_terminate(smf_ctx_t *ctx, int32_t value);
  int32_t smf_run_state(smf_ctx_t *ctx);

  static inline const smf_state_t *smf_get_current_leaf_state(const smf_ctx_t *ctx)
  {
    return ctx->current;
  }

  static inline const smf_state_t *smf_get_current_executing_state(const smf_ctx_t *ctx)
  {
    return ctx->executing;
  }

  static inline bool smf_is_terminated(const smf_ctx_t *ctx)
  {
    return ctx != NULL && ctx->terminate;
  }

#if SMF_INSTRUMENTATION
  void smf_set_hooks(smf_ctx_t *ctx, const smf_hooks_t *hooks);
#endif

#ifdef __cplusplus
}
#endif

#endif /* SMF_H */
