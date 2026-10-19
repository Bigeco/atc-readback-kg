# Results (figures and captions)

All figures: `docs/evaluation/figures/` (PDF for LaTeX, PNG for preview). Regenerate with `python src/evaluation/plot_eval.py`.
Setting unless noted: KATL LiveATC, 2024-08-26, 38 archives (GND 7, APP 19, DEP 12) transcribed and diarized by MOSS-ATC,
7,641 transmissions; extraction with Qwen3-4B-Instruct; KG `results/katl_moss38`.


Results are grouped by the three evaluation goals: (1) quality of the KG itself, (2) readback recognition, (3) situational awareness.

---

# (1) KG quality

## Figure 1. Intrinsic KG quality without gold labels (E0)

![](figures/fig1_e0_intrinsic.png)

**Caption.** (a) Structural and ontology-level quality: share of edges whose endpoint types match the ontology's
domain/range, instructions with a valid validity interval, readback edges that satisfy the speaker/flight/time
constraints, LLM instructions conforming to the ontology (OC; Text2KGBench), and TARGET/VIA edges pointing to an entity
in the airport KB. (b) Hallucination rates of the raw LLM output before validation: relation (RH, type outside the
ontology), subject (SH, callsign digits absent from the transmission) and object (OH, target or route absent from the
transmission).

**Results.** The KG has no schema or temporal violations (26,670 edges, 4,865 intervals, 337 readback edges), and no
flight holds two mutually superseding states at the same time. The LLM follows the ontology for 93.4% of instructions
and rarely invents an instruction type (RH 3.6%), but 14.8% of its callsigns and 15.3% of its targets are not supported
by the transmission; the validation step of M2b removes these before they enter the KG. 85.1% of instruction targets
resolve to the KATL airport KB; the rest are flagged as KB violations. The 100% rates follow from construction and show
that the graph is well-formed, not that its facts are correct (see Figure 2).

---

## Figure 2. Precision of KG facts against the source transmission (E2)

![](figures/fig2_e2_precision.png)

**Caption.** Precision of five fact types sampled from the KG (10 per relation, 50 in total, 11-archive KG), each
judged against the transcript of its source transmission. Strict: the fact is fully stated in the transcript; lenient:
also accepts a partially correct fact (right entity or number, but wrong role or an inferred detail). Error bars: Wilson
95% CI of the strict precision. Labels are provisional (single annotator, not yet adjudicated).

**Results.** Overall precision is 66% (strict; 95% CI 52-78%) and 78% (lenient). Route (VIA, 80%) and target (TARGET,
70% strict, 100% lenient) facts are the most reliable; every TARGET error names a place from the transmission but in the
wrong role (e.g. the route "Lima" or the entry point "Mike two" instead of the destination runway 9L). The weakest fact
type is the instruction type (50%): approach clearances such as "runway eight left, descend" and a hold-short ("three
eight Alpha, hold short of eight left") were typed TAXI_VIA. Flight attribution (ADDRESSES, 60%) fails when a number is
read as a callsign: the frequency readback "nineteen one" became flight UNK191 and "Delta twenty twenty four" became
DAL224. The intervals are wide (about +/-25 points per relation) because of the small sample, so differences between
relations are indicative only.

---

# (2) Readback recognition

## Figure 3. Readback-error detection under controlled injection (E3)

![](figures/fig3_e3_detection.png)

**Caption.** Detection rate of the readback check for seven error types injected into pilot readbacks that the system
had judged MATCH (200 pairs; text-level injection, same LLM extractor as the KG). The orange bar is the false-alarm rate
on the same readbacks without injection. An injection counts as detected when the system raises the alert expected
for it: a PARTIAL or MISMATCH readback status for place, value and hold-short errors, a different flight or a
SIMILAR_CALLSIGN flag for a changed callsign digit, and a NO_CALLSIGN flag for an omitted callsign. n is the number of
pairs to which an injection type applies. Error bars: Wilson 95% confidence intervals. Runway-side and taxiway
injections are applied only to pairs whose controller instruction contains a runway side or a taxiway.

**Results.** The check detects heading changes (44/44) and omitted hold-short instructions (11/11) without miss, and
callsign digit changes in 94.5% of cases, while flagging only 3.5% of unperturbed readbacks. Altitude changes (86.8%)
and omitted callsigns (85.8%) are detected in most cases. Spatial substitutions are the hardest: runway-side swaps
(78.1%) and taxiway substitutions (76.2%) are missed when the extractor does not recover the swapped place from the
readback. Because injection operates on clean text, these rates are upper bounds; on real recordings only about 42% of
MISMATCH alerts (8 of 19 sampled, 11 archives) correspond to genuine discrepancies.

---

## Figure 4. Effect of the revised readback comparison (E3)

![](figures/fig4_e3_baseline_vs_ours.png)

**Caption.** Detection rate of the baseline (Whisper transcripts, exact target/route match; KG `katl_all`) and of the
revised system (MOSS transcripts, set-based place match grounded in the controller's text, heading/altitude value
match; KG `katl_moss38`) on the four injection types measured in both runs (same extractor, 200 MATCH pairs each).
Error bars: Wilson 95% CI. n (baseline / ours): callsign digit 189/200, callsign omitted 168/169, taxiway 146/80,
runway side 7/32.

**Results.** The revised system detects taxiway substitutions in 76% of cases (baseline 33%), runway-side swaps in 78%
(baseline 0 of 7) and omitted callsigns in 86% (baseline 58%); callsign-digit detection stays above 90% in both. In the
baseline, a swapped place often left the comparison unchanged ("9 left at Mike 2" changed to "9 right" was still
MATCH), and numbers read as callsigns hid an omitted callsign ("descend 3000, Delta 583" without the callsign became
flight UNK3000). The comparison is not controlled: besides the transcription model and the comparison rule, the
injection procedure also changed. The baseline injected taxiway swaps into pairs without a taxiway and replaced
callsign words ("Delta 583" to "Victor 583"), which cannot be detected as a place error; the revised run injects only
where the controller instruction contains the element. Part of the taxiway gain is therefore due to the evaluation
procedure. The baseline also raised no false alarm on unperturbed pairs (0/200, vs. 3.5% for the revised system).

---

## Figure 5. Readback outcomes and alerts in the KATL temporal KG

![](figures/fig5_readback_alerts.png)

**Caption.** (a) Status of the 337 readback links (pilot transmission to the preceding controller transmission of the
same flight within 20 s). (b) Alerts raised by the rule layer (M5).

**Results.** Of the linked readbacks, 68% fully match, 22% omit at least one instruction that requires a readback and
10% state a different value or place. NO_READBACK (1,049) and SIMILAR_CALLSIGN (671) dominate the alerts, and neither is
a verified error count. For about a quarter of the NO_READBACK alerts (276 of 1,049, by a heuristic check) a pilot reply
containing the flight's number follows within 20 s, but the reply was attributed to another flight, mostly because a
number in it was read as the callsign (e.g. "Twenty three eighty five, Delta fourteen eighty nine" became flight
UNK2385). SIMILAR_CALLSIGN is a hint rule that fires whenever an addressed callsign is one digit edit away from a
recently active callsign of the same airline (e.g. DAL342 and DAL348); with many Delta flights on frequency it fires
often, and only 2 of the 671 involve a spurious flight. These counts are therefore upper bounds and should not be read
as error rates.

---

# (3) Situational awareness

## Situational awareness (no figure)

**Setting.** Competency questions CQ1, CQ2, CQ7 and CQ8 (E1) query the temporal KG for the state given by the
instructions: the instruction history of a flight, flights holding short at a taxiway in a time window, flights still
receiving ground instructions after hand-off to the tower, and the instructions valid at a time *t* (snapshot over the
validity intervals τ).

**Results.** All four queries are answerable and every answer is traceable to the source audio file and offset
(CQ1 48, CQ2 13, CQ7 77, CQ8 124 answers on the 38-archive KG). The temporal layer is consistent (Figure 1a: no invalid
interval, no flight holding two mutually superseding states). The correctness of the answers is not yet measured
against a gold timeline; the place facts the snapshot relies on have strict precision of 70-80% (Figure 2). Situational
awareness here means the state given by ATC instructions; without surveillance data the actual aircraft position is not
known.
