# Software-in-the-Loop (SIL) Sandbox Implementation Plan

> **Objective:** Establish an automated Software-in-the-Loop (SIL) test framework for the UWB-RTLS positioning system. This document outlines the architecture, directory structure, test scenarios, and step-by-step implementation guide in simple, clear technical terms so any engineer or AI agent can follow.

---

## 1. Motivation & Purpose

### 1.1. Limitations of Hardware-Only Testing
* Validating positioning algorithms (UKF, Mahalanobis prefilter, 50Hz stream cadence) on physical hardware requires flashing microcontrollers, setting up anchors, and manually moving the robot.
* Hard-to-reproduce corner cases (sudden acceleration, anchor obstruction, intermittent packet drops) are difficult and time-consuming to recreate consistently on the bench.
* Pull Requests (PRs) cannot be validated automatically in CI/CD without physical test rigs.

### 1.2. The SIL Solution
* **Compile 100% of the production C algorithm code** (`mw_filter.c`, `sys_sensor_fusion.c`, `mw_trilateration.c`) directly on host machines (Windows / Linux).
* **Simulate hardware and environment**: Inject synthetic UWB ranges, IMU sensor data, and virtual time without touching the production algorithm logic.
* **Fast and deterministic**: Runs thousands of cycles in less than one second, allowing instant automated regressions.

---

## 2. Operational Model

The test pipeline consists of three stages:

```text
[1. Test Scenarios]       --->  [2. Production C Algorithm]  --->  [3. Automated Evaluator]
(Trajectory, UWB ranges,        (mw_filter.c,                      (Assert: 50.0Hz cadence?
 IMU accelerations/gyro)         sys_sensor_fusion.c)               Zero deadlock? RMS error?)
```

1. **Input (Scenario):** Predefined or generated motion profiles providing ground truth positions, true ranges, and IMU angular rate/acceleration.
2. **Processing (C Harness):** Direct execution of production firmware algorithms inside a lightweight virtual RTOS/HAL environment.
3. **Evaluation (Python Runner):** Compares computed positions against ground truth, calculates timing jitter, and asserts pass/fail criteria.

---

## 3. Directory Structure

All SIL files reside under `tests/sil/`, keeping the production `firmware/` directory untouched:

```text
uwb-rtls/
├── firmware/                          # Production firmware (untouched)
│   └── uwb/
│       ├── middlewares/               # mw_filter.c, mw_trilateration.c
│       └── sys/                       # sys_sensor_fusion.c, positioning_config.h
│
└── tests/
    └── sil/                           # SIL Sandbox framework
        ├── CMakeLists.txt             # Native host build configuration
        ├── Makefile                   # Alternative simple make build
        ├── virtual_platform/          # RTOS & HAL virtualization
        │   ├── v_rtos.h / v_rtos.c    # Virtual FreeRTOS (osDelayUntil, queues)
        │   └── v_hal.h / v_hal.c      # Virtual HAL (HAL_GetTick, logging stubs)
        │
        ├── harness/                   # Execution harness
        │   └── sil_main.c             # Production loop runner and scenario loader
        │
        ├── scenarios/                 # Test scenarios (JSON/CSV)
        │   ├── 01_timing_50hz.json    # 50Hz steady cadence verification
        │   ├── 02_sudden_jump.json    # Sudden displacement deadlock recovery
        │   └── 03_nlos_spike.json     # Anchor multipath / outlier rejection
        │
        └── runner/                    # Python runner and test evaluation
            ├── run_all.py             # One-command execution of all scenarios
            └── evaluate.py            # Metrics calculation and report generator
```

---

## 4. Three Golden Test Scenarios

### Scenario 1: 50Hz Cadence Verification (`test_timing_50hz`)
* **Objective:** Ensure the system maintains a steady 20ms period (50Hz) without timing drift or frame drops.
* **Procedure:** Run a nominal 10-second simulation (500 cycles).
* **Pass Criteria:**
  * Average frequency: $50.0 \pm 0.2\text{ Hz}$.
  * Interval jitter standard deviation: $< 1.0\text{ ms}$.
  * Dropped packets: 0.

### Scenario 2: Sudden Motion Deadlock Recovery (`test_sudden_acceleration`)
* **Objective:** Verify that sudden tag acceleration ($> 1.5\text{ m}$) does not cause permanent prefilter rejection or freeze the UKF.
* **Procedure:** The tag moves steadily, then undergoes a sudden 2.0m displacement in one cycle.
* **Pass Criteria:**
  * UKF automatically recovers and tracks the new position within $\le 3\text{ UWB cycles}$ ($120\text{ ms}$).
  * Zero permanent deadlock; tracking resumes normally.

### Scenario 3: NLOS Anchor Outlier Rejection (`test_nlos_rejection`)
* **Objective:** Verify that a corrupted range (+3.0m offset on Anchor 2) is rejected by the Mahalanobis prefilter.
* **Procedure:** Inject a +3.0m bias on Anchor 2 for 2 seconds while the other anchors remain normal.
* **Pass Criteria:**
  * Anchor 2 is rejected by the prefilter.
  * Estimated tag position error remains within $< 15\text{ cm}$.

---

## 5. Implementation Roadmap

1. **Step 1: Virtual Platform (`virtual_platform/`)**
   * Implement virtual time tracking and non-blocking queue operations matching CMSIS-RTOS v2 APIs.
   * Provide logging redirection and timing utilities.
2. **Step 2: Execution Harness (`harness/` & build system)**
   * Compile production C files (`mw_filter.c`, `mw_trilateration.c`, `sys_sensor_fusion.c`).
   * Read scenarios, step the virtual clock, execute the filter pipeline, and record telemetry.
3. **Step 3: Python Runner & Test Suite (`runner/`)**
   * Implement scenario generation, CSV output parsing, and automated pass/fail verification.
4. **Step 4: Continuous Integration Setup**
   * Add a GitHub Actions workflow to run SIL automatically on PRs into `staging` or `develop`.

---

## 6. Definition of Done

The SIL Sandbox implementation is complete when:
- [ ] `tests/sil/` builds cleanly on host with a single build command.
- [ ] `python tests/sil/runner/run_all.py` executes and passes all 3 golden test scenarios.
- [ ] Entire test suite executes in under 5 seconds.
- [ ] No production firmware files in `firmware/` are modified or compromised.
