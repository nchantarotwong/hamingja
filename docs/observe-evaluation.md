# Observe-mode evaluation

Keep detector thresholds unchanged until observations justify a specific
adjustment. A model upgrade alone is not evidence that a tripwire should fire
earlier or later. Use this protocol with the existing `hamingja report`; it
adds no telemetry, hook state, or enforcement authority.

## Collect comparable windows

1. Record the package version, runtime version, model if known, and relevant
   configuration overrides locally. Use observe mode for mechanical detectors;
   separately record any operator stop or checkpoint-denial configuration.
2. Exercise debugging, feature implementation, research/navigation, and
   parallel work in separate, bounded windows where practical. Record how many
   tasks and sessions actually ran, including sessions with no advisories.
3. At the end of each window, run `hamingja report --since-hours 4 --json`
   (choose a duration matching the window). Keep the aggregate report, not the
   underlying audit log. Do not reset shared logs to isolate an experiment.
4. Record operator judgments using only the categories below. Leave unknown
   observations unknown. A successful test does not prove that an advisory
   helped; a session with no verdicts does not prove that hooks covered its tools.
5. Compare matched task shapes before and after an upgrade. Use non-overlapping
   windows; the rolling four-hour report must not be summed across overlapping
   samples. Label mixed runtime/model windows as mixed rather than attributing
   their results to a single model.

## Aggregate record

Copy this table per window. Keep counts and categorical labels only; never
commit prompts, commands, repository paths, tool output, session IDs, or captured
sessions. Runtime/model version labels are optional when they are not known.

| Field | Value |
|---|---|
| Window / package / runtime / model | date, hours, versions or unknown |
| Task shape | debugging / implementation / research / parallel / mixed |
| Tasks attempted / completed | counts or unknown |
| Total sessions observed manually | count or unknown |
| Sessions with verdicts | report `sessions` |
| Nudges / would-blocks / blocks | report counts |
| Detector / response counts | report `by_detector` / `by_response` |
| Advisory judgments | helpful / disruptive / neutral / unknown counts |
| Recovery after intervention | succeeded / failed / unknown counts |
| Hook coverage | synthetic-only / runtime-observed / unknown |
| Configuration differs from defaults | yes / no / unknown |

Each judged advisory gets one category. Recovery counts cover interventions,
not every task. Keep operator-budget messages separate if they are not present
in the report; do not infer their counts from detector totals. The audit log is
bounded, so a saturated window can omit older events; shorten the window and
label incomplete samples. There is no all-tool-call denominator in the report:
these counts cannot establish a false-positive rate on their own.

## Decision rule

Fix any reproduced unsafe denial immediately with a synthetic regression.
For tuning, seek repeated disruptive advisories across independent tasks and
retain known-loop counterexamples before changing a default. Compare helpful
interventions and successful recovery as well as noise. Do not trade away a
mechanically proven tripwire to improve aggregate counts. Keep inconclusive
samples and thresholds unchanged; a long soak is not required to ship a
reproduced fail-open fix.

## Initial baseline, 2026-09-10

The installed `hamingja report --since-hours 4 --json` returned 2 advisories
from `python_command`, 1 session with verdicts, 0 would-blocks, and 0 blocks.
Both responses were `advise`. This is a pre-upgrade aggregate snapshot, not
an evaluation of the revised profiles or a model comparison. Total task/session
counts, operator usefulness judgments, and recovery outcomes were not collected.
It does not justify numerical tuning. Future windows should use the complete
record above.
