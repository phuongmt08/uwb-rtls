#include "smf.h"

enum test_event
{
  TEST_EVENT_NONE = 0,
  TEST_EVENT_NEXT,
  TEST_EVENT_PARENT,
  TEST_EVENT_SELF,
  TEST_EVENT_TERMINATE
};

typedef struct
{
  smf_ctx_t       smf;
  enum test_event event;
  char            trace[64];
  size_t          trace_len;
} test_machine_t;

enum test_state_id
{
  TEST_ROOT = 0,
  TEST_PARENT,
  TEST_A,
  TEST_B,
  TEST_OUTSIDE,
  TEST_STATE_COUNT
};

static const smf_state_t states[TEST_STATE_COUNT];

static bool trace_equals(const test_machine_t *machine, const char *expected)
{
  size_t index = 0U;
  while (machine->trace[index] != '\0' && expected[index] != '\0')
  {
    if (machine->trace[index] != expected[index])
    {
      return false;
    }
    index++;
  }
  return machine->trace[index] == expected[index];
}

static void clear_machine(test_machine_t *machine)
{
  unsigned char *bytes = (unsigned char *) machine;
  for (size_t index = 0U; index < sizeof(*machine); index++)
  {
    bytes[index] = 0U;
  }
}

static void trace(test_machine_t *machine, char marker)
{
  if (machine->trace_len + 1U >= sizeof(machine->trace))
  {
    return;
  }
  machine->trace[machine->trace_len++] = marker;
  machine->trace[machine->trace_len]   = '\0';
}

static void root_entry(void *obj)
{
  trace(obj, 'R');
}
static void parent_entry(void *obj)
{
  trace(obj, 'P');
}
static void parent_exit(void *obj)
{
  trace(obj, 'p');
}
static void a_entry(void *obj)
{
  trace(obj, 'A');
}
static void a_exit(void *obj)
{
  trace(obj, 'a');
}
static void b_entry(void *obj)
{
  trace(obj, 'B');
}
static void b_exit(void *obj)
{
  trace(obj, 'b');
}
static void outside_entry(void *obj)
{
  trace(obj, 'O');
}

static smf_state_result_t parent_run(void *obj)
{
  test_machine_t *machine = obj;
  if (machine->event == TEST_EVENT_PARENT)
  {
    machine->event = TEST_EVENT_NONE;
    smf_set_state(SMF_CTX(machine), &states[TEST_OUTSIDE]);
    return SMF_EVENT_HANDLED;
  }
  return SMF_EVENT_PROPAGATE;
}

static smf_state_result_t a_run(void *obj)
{
  test_machine_t *machine = obj;
  if (machine->event == TEST_EVENT_NEXT)
  {
    machine->event = TEST_EVENT_NONE;
    smf_set_state(SMF_CTX(machine), &states[TEST_B]);
    return SMF_EVENT_HANDLED;
  }
  return SMF_EVENT_PROPAGATE;
}

static smf_state_result_t b_run(void *obj)
{
  test_machine_t *machine = obj;
  if (machine->event == TEST_EVENT_SELF)
  {
    machine->event = TEST_EVENT_NONE;
    smf_set_state(SMF_CTX(machine), &states[TEST_B]);
    return SMF_EVENT_HANDLED;
  }
  if (machine->event == TEST_EVENT_TERMINATE)
  {
    machine->event = TEST_EVENT_NONE;
    smf_set_terminate(SMF_CTX(machine), 7);
    return SMF_EVENT_HANDLED;
  }
  return SMF_EVENT_PROPAGATE;
}

static const smf_state_t states[TEST_STATE_COUNT] = {
  [TEST_ROOT] = SMF_STATE(root_entry, NULL, NULL, NULL, SMF_REF(states, TEST_PARENT)),
  [TEST_PARENT] =
    SMF_STATE(parent_entry, parent_run, parent_exit, SMF_REF(states, TEST_ROOT), SMF_REF(states, TEST_A)),
  [TEST_A]       = SMF_STATE(a_entry, a_run, a_exit, SMF_REF(states, TEST_PARENT), NULL),
  [TEST_B]       = SMF_STATE(b_entry, b_run, b_exit, SMF_REF(states, TEST_PARENT), NULL),
  [TEST_OUTSIDE] = SMF_STATE(outside_entry, NULL, NULL, SMF_REF(states, TEST_ROOT), NULL),
};

#if defined(_WIN32)
#define TEST_EXPORT __declspec(dllexport)
#else
#define TEST_EXPORT
#endif

TEST_EXPORT int smf_self_test(void)
{
  test_machine_t machine;
  clear_machine(&machine);

  smf_set_initial(SMF_CTX(&machine), &states[TEST_ROOT]);
  if (smf_get_current_leaf_state(SMF_CTX(&machine)) != &states[TEST_A])
    return 1;
  if (!trace_equals(&machine, "RPA"))
    return 2;

  machine.event = TEST_EVENT_NEXT;
  if (smf_run_state(SMF_CTX(&machine)) != 0)
    return 3;
  if (smf_get_current_leaf_state(SMF_CTX(&machine)) != &states[TEST_B])
    return 4;
  if (!trace_equals(&machine, "RPAaB"))
    return 5;

  machine.event = TEST_EVENT_SELF;
  if (smf_run_state(SMF_CTX(&machine)) != 0)
    return 6;
  if (smf_get_current_leaf_state(SMF_CTX(&machine)) != &states[TEST_B])
    return 7;
  if (!trace_equals(&machine, "RPAaBbB"))
    return 8;

  machine.event = TEST_EVENT_PARENT;
  if (smf_run_state(SMF_CTX(&machine)) != 0)
    return 9;
  if (smf_get_current_leaf_state(SMF_CTX(&machine)) != &states[TEST_OUTSIDE])
    return 10;
  if (!trace_equals(&machine, "RPAaBbBbpO"))
    return 11;

  clear_machine(&machine);
  smf_set_initial(SMF_CTX(&machine), &states[TEST_B]);
  machine.event = TEST_EVENT_TERMINATE;
  if (smf_run_state(SMF_CTX(&machine)) != 7)
    return 12;
  if (smf_run_state(SMF_CTX(&machine)) != 7)
    return 13;

  return 0;
}
