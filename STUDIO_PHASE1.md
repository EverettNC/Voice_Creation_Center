# Live Studio — Phase 1

The **Voice Creation Center is the recording and approval station** — not
AlphaVox. Phase 1 gives Everett a small, honest workflow to record takes,
validate them, approve the good ones, and export only approved takes to the
AlphaVox phrase pack.

## The Rule

> **Raw takes stay in the Voice Creation Center.**
> **Approved takes export to the AlphaVox phrase pack:**
> `/Users/EverettN/packs/phrases/alphavox_cycled`

Raw and processed takes never leave the Center on their own. The only thing
that crosses the line is an *approved* take, and only a **copy** of it — the
original raw take stays put.

## Files

| File | Purpose |
|------|---------|
| `studio/live_studio.py` | Session model + `LiveStudio` service (the workflow) |
| `studio/test_live_studio.py` | Smoke tests proving the workflow end to end |

The export directory constant lives in `live_studio.py`:

```python
ALPHAVOX_APPROVED_EXPORT_DIR = Path("/Users/EverettN/packs/phrases/alphavox_cycled")
```

## The session model

A `StudioSession` carries exactly the fields Phase 1 needs:

`id`, `speaker`, `target_phrase`, `phrase_key`, `take_number`, `raw_path`,
`processed_path`, `transcript`, `validation_status`, `approved_by_everett`,
`exported_to`, `created_at` (plus `being`, `language`, and a `state`).

## Workflow states

```
created → take_registered → validated → approved → exported
                                      ↘ rejected
```

## Usage

```python
from studio.live_studio import LiveStudio

studio = LiveStudio()              # exports to the real AlphaVox pack
# studio = LiveStudio(export_dir="/tmp/test_out")   # for testing

# 1. Open a session for a target phrase
session = studio.create_session("I love you", speaker="Everett")

# 2. Attach the raw take you recorded (the wav must already exist).
#    Phase 1 does NOT capture the mic — record with your own tool first.
studio.register_raw_take(session.id, "/Users/EverettN/incoming/take1.wav")

# 3. Record a validation result. Reuses voice_validator on the transcript.
studio.mark_validation(session.id, transcript="I love you")
#    Pass passed=True/False to combine a human judgment with the text check.

# 4. Everett approves (or rejects)
studio.approve(session.id)         # only validated+passing takes can approve
# studio.reject(session.id)

# 5. Export the approved take → AlphaVox phrase pack.
#    Copies the wav into ALPHAVOX_APPROVED_EXPORT_DIR and registers it
#    into the express index (voice_path_registrar.register_single_file).
studio.export_approved(session.id)
print(session.exported_to)
```

## Microphone capture

Phase 1 has **no microphone capture**. This repo has no audio-capture support,
so `LiveStudio.capture_take(...)` is an honest stub that raises
`NotImplementedError` — it does not fake a recording. A later phase can wire
real capture there and have it call `register_raw_take()` with the captured wav.

## What it reuses

- `engines/voice_validator.py` → `validate_text()` validates the transcript.
- `engines/voice_path_registrar.py` → `register_single_file()` registers the
  exported wav into `express/express_index.json` (no APIs invented).

## Running the tests

```bash
python3 studio/test_live_studio.py      # standalone runner (no deps)
# or, if pytest is installed:
pytest studio/test_live_studio.py
```

Tests use a temp export dir and a temp express index — they never touch the
real `/Users/EverettN` path or the Center's real `express_index.json`.
