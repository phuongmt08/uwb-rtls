/*
 * Portable adaptation of the Zephyr State Machine Framework.
 *
 * Copyright 2021 The Chromium OS Authors
 * SPDX-License-Identifier: Apache-2.0
 */

#include "smf.h"

#include <stddef.h>

#if SMF_INSTRUMENTATION
#define SMF_ACTION_HOOK(_ctx, _state, _action)                     \
  do                                                               \
  {                                                                \
    if ((_ctx)->hooks != NULL && (_ctx)->hooks->on_action != NULL) \
    {                                                              \
      (_ctx)->hooks->on_action((_ctx), (_state), (_action));       \
    }                                                              \
  } while (0)
#define SMF_TRANSITION_HOOK(_ctx, _source, _destination)               \
  do                                                                   \
  {                                                                    \
    if ((_ctx)->hooks != NULL && (_ctx)->hooks->on_transition != NULL) \
    {                                                                  \
      (_ctx)->hooks->on_transition((_ctx), (_source), (_destination)); \
    }                                                                  \
  } while (0)
#define SMF_ERROR_HOOK(_ctx, _code)                               \
  do                                                              \
  {                                                               \
    if ((_ctx)->hooks != NULL && (_ctx)->hooks->on_error != NULL) \
    {                                                             \
      (_ctx)->hooks->on_error((_ctx), (_code));                   \
    }                                                             \
  } while (0)
#else
#define SMF_ACTION_HOOK(_ctx, _state, _action) \
  do                                           \
  {                                            \
    (void) (_ctx);                             \
    (void) (_state);                           \
    (void) (_action);                          \
  } while (0)
#define SMF_TRANSITION_HOOK(_ctx, _source, _destination) \
  do                                                     \
  {                                                      \
    (void) (_ctx);                                       \
    (void) (_source);                                    \
    (void) (_destination);                               \
  } while (0)
#define SMF_ERROR_HOOK(_ctx, _code) \
  do                                \
  {                                 \
    (void) (_ctx);                  \
    (void) (_code);                 \
  } while (0)
#endif

static void clear_run_flags(smf_ctx_t *ctx)
{
  ctx->new_state = false;
  ctx->terminate = false;
  ctx->in_exit   = false;
  ctx->handled   = false;
}

static bool is_descendant_of(const smf_state_t *state, const smf_state_t *ancestor)
{
  for (const smf_state_t *candidate = state; candidate != NULL; candidate = candidate->parent)
  {
    if (candidate == ancestor)
    {
      return true;
    }
  }
  return false;
}

static const smf_state_t *child_of(const smf_state_t *state, const smf_state_t *parent)
{
  const smf_state_t *candidate = state;

  while (candidate != NULL)
  {
    if (candidate->parent == parent)
    {
      return candidate;
    }
    candidate = candidate->parent;
  }
  return NULL;
}

static const smf_state_t *least_common_ancestor(const smf_state_t *source,
                                                const smf_state_t *destination)
{
  for (const smf_state_t *ancestor = source->parent; ancestor != NULL; ancestor = ancestor->parent)
  {
    if (is_descendant_of(destination, ancestor))
    {
      return ancestor;
    }
  }
  return NULL;
}

static bool execute_entry_path(smf_ctx_t         *ctx,
                               const smf_state_t *destination,
                               const smf_state_t *topmost)
{
  if (destination == topmost)
  {
    return false;
  }

  for (const smf_state_t *state = child_of(destination, topmost); state != NULL && state != destination;
       state                    = child_of(destination, state))
  {
    ctx->executing = state;
    if (state->entry != NULL)
    {
      SMF_ACTION_HOOK(ctx, state, SMF_ACTION_ENTRY);
      state->entry(ctx);
      if (ctx->terminate)
      {
        ctx->executing = ctx->current;
        return true;
      }
    }
  }

  ctx->executing = destination;
  if (destination->entry != NULL)
  {
    SMF_ACTION_HOOK(ctx, destination, SMF_ACTION_ENTRY);
    destination->entry(ctx);
    if (ctx->terminate)
    {
      ctx->executing = ctx->current;
      return true;
    }
  }
  ctx->executing = ctx->current;
  return false;
}

static bool execute_exit_path(smf_ctx_t *ctx, const smf_state_t *topmost)
{
  const smf_state_t *saved_executing = ctx->executing;

  for (const smf_state_t *state = ctx->current; state != NULL && state != topmost; state = state->parent)
  {
    if (state->exit != NULL)
    {
      ctx->executing = state;
      SMF_ACTION_HOOK(ctx, state, SMF_ACTION_EXIT);
      state->exit(ctx);
      if (ctx->terminate)
      {
        ctx->executing = saved_executing;
        return true;
      }
    }
  }
  ctx->executing = saved_executing;
  return false;
}

void smf_set_initial(smf_ctx_t *ctx, const smf_state_t *initial_state)
{
  if (ctx == NULL || initial_state == NULL)
  {
    return;
  }

  while (initial_state->initial != NULL)
  {
    initial_state = initial_state->initial;
  }

  ctx->current         = NULL;
  ctx->previous        = NULL;
  ctx->executing       = NULL;
  ctx->terminate_value = 0;
  ctx->new_state       = false;
  ctx->terminate       = false;
  ctx->in_exit         = false;
  ctx->handled         = false;
#if SMF_INSTRUMENTATION
  ctx->hooks = NULL;
#endif
  ctx->current   = initial_state;
  ctx->executing = initial_state;

  const smf_state_t *root = child_of(initial_state, NULL);
  if (root != NULL && root->entry != NULL)
  {
    ctx->executing = root;
    SMF_ACTION_HOOK(ctx, root, SMF_ACTION_ENTRY);
    root->entry(ctx);
    if (ctx->terminate)
    {
      return;
    }
  }

  (void) execute_entry_path(ctx, initial_state, root);
  ctx->executing = ctx->current;
}

void smf_set_state(smf_ctx_t *ctx, const smf_state_t *new_state)
{
  if (ctx == NULL)
  {
    return;
  }
  if (new_state == NULL)
  {
    SMF_ERROR_HOOK(ctx, SMF_ERR_NULL_TRANSITION);
    return;
  }
  if (ctx->in_exit)
  {
    SMF_ERROR_HOOK(ctx, SMF_ERR_TRANSITION_IN_EXIT);
    return;
  }

  const smf_state_t *source = ctx->executing;
  const smf_state_t *topmost;

  if (source != new_state && source->parent == new_state->parent)
  {
    topmost = source->parent;
  }
  else if (is_descendant_of(source, new_state))
  {
    topmost = new_state;
  }
  else if (is_descendant_of(new_state, source))
  {
    topmost = source;
  }
  else
  {
    topmost = least_common_ancestor(source, new_state);
  }

  ctx->in_exit   = true;
  ctx->new_state = true;
  if (execute_exit_path(ctx, topmost))
  {
    return;
  }

  if (source == new_state && new_state->exit != NULL)
  {
    SMF_ACTION_HOOK(ctx, new_state, SMF_ACTION_EXIT);
    new_state->exit(ctx);
    if (ctx->terminate)
    {
      return;
    }
  }
  ctx->in_exit = false;

  if (source == new_state && new_state->entry != NULL)
  {
    SMF_ACTION_HOOK(ctx, new_state, SMF_ACTION_ENTRY);
    new_state->entry(ctx);
    if (ctx->terminate)
    {
      return;
    }
  }

  while (new_state->initial != NULL)
  {
    new_state = new_state->initial;
  }

  ctx->previous  = ctx->current;
  ctx->current   = new_state;
  ctx->executing = new_state;
  SMF_TRANSITION_HOOK(ctx, ctx->previous, ctx->current);
  (void) execute_entry_path(ctx, new_state, topmost);
  ctx->executing = ctx->current;
}

void smf_set_terminate(smf_ctx_t *ctx, int32_t value)
{
  if (ctx == NULL || value == 0)
  {
    return;
  }
  ctx->terminate       = true;
  ctx->terminate_value = value;
}

int32_t smf_run_state(smf_ctx_t *ctx)
{
  if (ctx == NULL || ctx->current == NULL)
  {
    return 0;
  }
  if (ctx->terminate)
  {
    return ctx->terminate_value;
  }

  clear_run_flags(ctx);
  ctx->executing = ctx->current;
  if (ctx->current->run != NULL)
  {
    SMF_ACTION_HOOK(ctx, ctx->current, SMF_ACTION_RUN);
    if (ctx->current->run(ctx) == SMF_EVENT_HANDLED)
    {
      ctx->handled = true;
    }
  }

  if (!ctx->terminate && !ctx->new_state && !ctx->handled)
  {
    for (const smf_state_t *state = ctx->current->parent; state != NULL; state = state->parent)
    {
      ctx->executing = state;
      if (state->run != NULL)
      {
        SMF_ACTION_HOOK(ctx, state, SMF_ACTION_RUN);
        if (state->run(ctx) == SMF_EVENT_HANDLED)
        {
          ctx->handled = true;
        }
        if (ctx->terminate || ctx->new_state || ctx->handled)
        {
          break;
        }
      }
    }
  }

  ctx->executing = ctx->current;
  return ctx->terminate ? ctx->terminate_value : 0;
}

#if SMF_INSTRUMENTATION
void smf_set_hooks(smf_ctx_t *ctx, const smf_hooks_t *hooks)
{
  if (ctx != NULL)
  {
    ctx->hooks = hooks;
  }
}
#endif
