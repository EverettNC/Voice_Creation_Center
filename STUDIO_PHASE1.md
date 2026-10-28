# Live Studio — Phase 1

The **Voice Creation Center is the recording and approval station** — not
the being. Phase 1 gives the reviewer a small, honest workflow to record takes,
validate them, approve the good ones, and export only approved takes to the
being's phrase pack.

## The Rule

> **Raw takes stay in the Voice Creation Center.**
> **Approved takes export to the being's phrase pack:**
> the folder the being supplies (`export_dir`)

Raw and processed takes never leave the Center on their own. The only thing
that crosses the line is an *approved* take, and only a **copy** of it — the
original raw take stays put.

## Files

| File | Purpose |
|------|---------|
| `studio/live_studio.py` | Session model + `LiveStudio` service (the workflow) |
| `studio/test_live_studio.py` | Smoke tests proving the workflow end to end |

The export directory is passed to `LiveStudio` by the being:

```python
LiveStudio(export_dir="<the being's phrase folder>")
```

## The session model

A `StudioSession` carries exactly the fields Phase 1 needs:

`id`, `speaker`, `target_phrase`, `phrase_key`, `take_number`, `raw_path`,
`processed_path`, `transcript`, `validation_status`, `approved`,
`exported_to`, `created_at` (plus `being`, `language`, and a `state`).

## Workflow states

```
created → take_registered → validated → approved → exported
                                      ↘ rejected
```

## Usage

```python
from studio.live_studio import LiveStudio

studio = LiveStudio(export_dir="<the being's phrase folder>")
# studio = LiveStudio(export_dir="/tmp/test_out")   # for testing

# 1. Open a session for a target phrase
session = studio.create_session("I love you", speaker="<speaker>", being="<being name>")

# 2. Attach the raw take you recorded (the wav must already exist).
#    Phase 1 does NOT capture the mic — record with your own tool first.
studio.register_raw_take(session.id, "/path/to/incoming/take1.wav")

# 3. Record a validation result. Reuses voice_validator on the transcript.
studio.mark_validation(session.id, transcript="I love you")
#    Pass passed=True/False to combine a human judgment with the text check.

# 4. The reviewer approves (or rejects)
studio.approve(session.id)         # only validated+passing takes can approve
# studio.reject(session.id)

# 5. Export the approved take → the being's phrase pack.
#    Copies the wav into export_dir and registers it
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

Tests use a temp export dir and a temp express index — they never touch a
real export folder or the Center's real `express_index.json`.
