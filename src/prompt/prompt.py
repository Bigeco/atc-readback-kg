########################## M2: ontology-grounded extraction ###########################

EXTRACTION_SYS_PROMPT = """
You convert one U.S. air traffic control radio transmission into structured ATC concepts.
You must follow the ontology exactly and never invent information that is not in the transmission.
""".strip()


EXTRACTION_PROMPT = """
[Task]
Read one radio transmission (an automatic transcript, so it may contain recognition errors) and return
the speaker role, the callsign, and every ATC instruction it contains, using only the allowed types.

[Allowed instruction types]
{command_types}

[Rules]
- speaker: "ATCO" (controller) or "PILOT". Controllers usually start with the callsign; pilots reading back usually end with it.
  A speaker hint from a rule-based detector is given; override it only if the text clearly contradicts it.
- callsign: the callsign as spoken, e.g. "Delta 2642" (digits only, drop hyphens). Controllers often abbreviate to the
  number only ("1606, Atlanta Ground, ...") -> return just "1606". Use null if no callsign is spoken.
- A pilot readback repeats the controller's instructions: extract them with the same types as the controller would.
- target: the runway ("8R", "26L"), taxiway ("L3", "M", "F1"), ramp ("Ramp 5") or facility ("Tower") the instruction refers to.
  Write taxiways as letter + optional number (Lima 3 -> "L3", Foxtrot -> "F"). Write runways as number + L/R/C.
- route: for TAXI_VIA only, the ordered list of taxiways (e.g. ["M", "L3"]). Otherwise [].
- value: frequency for CONTACT/MONITOR ("119.1"), traffic description for GIVE_WAY/FOLLOW, else null.
- Recognition errors to repair: "fox shot"/"fox tractor" = Foxtrot, "9 or left" = 9 left, "hold for the Lima 3" = hold short of Lima 3,
  "19-1" after monitor/contact tower = 119.1. Repair only these kinds of obvious errors.
- REQUEST is only for a pilot asking for something (ready to taxi, request ...). A controller question is not an instruction.
- A pilot readback without a verb still contains instructions: "Victor to 1, Delta 22" is TAXI_VIA with route ["V"].
- Pure acknowledgements ("roger", "see you") are ACK. Chatter without ATC content gets an empty list.
- Never use a number like "3737", "757" or "7-6" as target: aircraft types and spot numbers are values, not targets.

[Output Format]
Return only JSON:
{{"speaker": "ATCO" | "PILOT", "callsign": string | null,
  "instructions": [{{"type": string, "target": string | null, "route": [string], "value": string | null, "qualifier": string | null}}]}}

[Examples]
{examples}

[Input]
speaker hint: {role_hint}
transmission: {text}
output:
""".strip()


# Few-shot examples are verbatim controller/pilot phraseology from FAA JO 7110.65BB EXAMPLE blocks
# (configs/fewshot_examples.json, section-checked by src/kb/build_ontology.py), not hand-written transmissions.
def _load_examples():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "configs" / "fewshot_examples.json"
    return [{"role_hint": ex["role_hint"], "text": ex["text"], "output": ex["output"]}
            for ex in json.loads(path.read_text(encoding="utf-8"))["examples"]]


EXTRACTION_EXAMPLES = _load_examples()


########################## E2: evidence judge (optional second opinion) ###########################

EVIDENCE_JUDGE_SYS_PROMPT = """
You audit facts in an air traffic control knowledge graph against the transcript they were extracted from.
""".strip()


EVIDENCE_JUDGE_PROMPT = """
[Claim]
{claim}

[Evidence transcript(s)]
{evidence}

[Question]
Is the claim supported by the evidence? Notation used in claims:
- Taxiways are written as the ICAO letter + number: L3 = "Lima 3", M = "Mike", F1 = "Foxtrot 1".
- Runways are number + side: 8R = "8 right", 9L = "9 left".
- Flights are airline code + number: DAL2642 = "Delta 2642" (also "26-42" or just "2642"); UNK = airline not spoken.
Treat obvious speech recognition variants as equivalent ("fox shot" = Foxtrot, "9 or left" = 9 left).
Use "partial" when the claim needs a reasonable repair of a recognition error (e.g. "hold for the Lima 3" read as hold short).
Answer with JSON only: {{"label": "explicit" | "partial" | "unsupported", "reason": string}}
""".strip()
