# AM Safety Principles

AM is a research prototype. It is **not** a medical device and its outputs are not
clinical diagnoses.

## 1. Thresholds are demonstration values

- All alert bands live in `config/thresholds.demo.yaml` and are labelled
  `DEMO — NOT CLINICALLY VALIDATED`. The label is copied onto every alert.
- The bands are statistical deviations from the astronaut's **own** baseline
  (robust z-scores). They are not clinical emergency thresholds.
- Qualified medical professionals must review and replace them before any operational use.

## 2. Sanity bounds are not reference ranges

`plausible_min` / `plausible_max` in the metric registry are wide engineering bounds. They
reject sensor faults and entry errors (for example, heart rate 0 or 900). They are never
used to classify health status.

## 3. Alerts are deterministic

- Alert severity (`NORMAL`, `WATCH`, `WARNING`, `CRITICAL`) is computed by
  `am/anomaly/engine.py`. No LLM is involved.
- Each alert stores:
  - the rule ID
  - the threshold version
  - the baseline profile it was compared against
  - the measurement IDs that support it (`alert_evidence`)
- An unacknowledged alert reports its peak severity in the overview status, so a critical
  episode stays visible until someone reviews it.
- Alerts are never auto-resolved. A named person must acknowledge and resolve them, and a
  resolution note is mandatory. If severity rises after acknowledgement, the alert returns
  to `ACTIVE`.

### Classification pipeline

1. Rejected data is skipped.
2. The baseline for the **same context** is selected (rest, exercise, sleep, and so on).
   A reading is never compared against a different context. With no matching baseline,
   the status is `NO_BASELINE`.
3. Deviation is computed: z = (x − median) / scale, where
   scale = max(1.4826·MAD, (p95 − p05)/3.29, |median|·fraction).
   The p05–p95 term keeps the scale realistic on quantized data such as integer bpm.
   - **Provisional** baselines use a small-sample predictive scale: the one-sided 95%
     upper confidence bound of the SD, combined with the standard error of the median.
4. Persistence gating:
   - `WATCH` and `WARNING` each need 2 same-direction deviations within the lookback window.
   - `CRITICAL` needs 3.
   - A single reading at or above the `WARNING` band is still surfaced as `WATCH`, never
     hidden.
5. Degraded-quality data is capped at `WATCH`.

## 4. Baselines cannot normalize deterioration

The rules are in `config/baseline_policy.yaml` and implemented in
`am/baseline/service.py`:

- **Stages:**
  - `provisional`: at least 20 samples spanning at least 1 hour. A ~3-hour window
    qualifies only as provisional.
  - `established`: at least 200 samples spanning at least 72 hours.
- **Eligible data:** only good-quality readings. Once a baseline exists, only readings
  classified `NORMAL` against it are eligible.
- **Rate limit:** each adaptation step may move the median by at most 0.5 scale units.
- **Freezes:** the baseline **freezes** instead of adapting when:
  - an open alert of `WARNING` or higher exists on the stream
  - the median of new data drifts more than 1.0 scale unit
  - the cumulative shift from the anchor exceeds 2.0 scale units (this blocks slow creep)
- **Manual actions:** unfreezing or rebuilding requires a named actor and a reason.
- **Audit:** every baseline change is written to `baseline_audit` and `audit_log`.

## 5. Simulated data is always labelled

Generator output (`am/simulation/generator.py`) is written with:

- source kind `simulated`
- `is_simulated = true` on the astronaut, mission, measurements and alerts

The distributions are arbitrary demonstration parameters. They are not physiological
norms.

## 6. The LLM's role

The implementation is `am/ai/`.

- **Read-only access:** the model reads stored data only through read-only tools
  (`am/ai/tools.py`). It has no write access: it cannot change alerts, baselines or memory.
- **No arithmetic:** comparisons such as the difference from the median, its direction
  and the percentage are computed deterministically and passed to the model.
- **No guessing:** tools reject ambiguous calls (missing context, invalid mission day)
  instead of guessing. Point-in-time questions use a deterministic nearest-reading lookup.
- **Traceability:** each answer stores the tool calls and the record IDs it used.
- **Grounding check:** numbers in an answer that do not appear in the retrieved data are
  flagged in the UI. Limitation: this cannot detect a real number attributed to the wrong
  time or indicator.
- **Prompt rules:** the system prompt forbids diagnosis, prescriptions, reassurance,
  unprompted mechanisms and treating correlation as causation. Compliance by a 7B model is
  imperfect, which is why the deterministic layers above exist.
- **Fallback:** if the model is unavailable, a rule-based summary is shown instead.
- **Memory writes:** mission memory is written only by deterministic code.
