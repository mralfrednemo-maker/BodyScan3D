# Task #3 Artifact: Capture UX — Four-State Review Outcomes + Intervention Telemetry
**Task:** Capture UX: four-state review outcomes + intervention telemetry
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `capture.html` modifications

## What This Implements

### CX-1 / CX-4: Four-State Review Outcomes
- `screen-done` now shows four outcome buttons instead of generic "Capture complete"
- Outcomes: USABLE (green), USABLE_WITH_FLAGS (amber), WORTH_PATCH (blue), RETRY_RECOMMENDED (red)
- Each button has a label explaining the action
- Selection triggers appropriate next step:
  - USABLE/USABLE_WITH_FLAGS → redirect to review browser
  - WORTH_PATCH → startPatchCapture() → patch screen
  - RETRY_RECOMMENDED → increment retryCount + reload

### CX-4: Patch Capture Offer
- WORTH_PATCH starts patch capture mode (`startPatchCapture()`)
- Camera restarts, patch progress shown (`0 / 10` frames)
- PATCH_TARGET = 10 additional frames
- Auto mode auto-starts for patch capture
- "Done — Send Patches" button appears when target reached
- `onPatchDone()` finalizes and redirects to review

### CX-6 / CX-7: Intervention Counters
- `autoModeCount`: incremented each time auto mode is entered (not per-frame)
- `manualShutterCount`: incremented on each manual shutter click
- `retryCount`: incremented when user selects RETRY_RECOMMENDED
- Counters sent to server via frame upload form data (first 3 frames of each batch)
- Also included in manifest JSON on first frame

### M1/M2 Ratio Tracking
- autoModeCount and manualShutterCount sent in manifest
- Server stores in capture_metadata (auto_mode_count, manual_shutter_count, retry_count)
- Available at `/api/capture/bundle/:scanId` for operator review

## Key Code Changes

**capture.html:**
- CSS: `.outcome-buttons`, `.outcome-btn.*` styles, `#screen-patch`
- HTML: outcome buttons container added to `#screen-done`, `#screen-patch` div
- JS state: `autoModeCount`, `manualShutterCount`, `retryCount`, `patchMode`, `patchCaptured`, `PATCH_TARGET`
- JS functions: `showOutcomeButtons()`, `onOutcomeSelected()`, `startPatchCapture()`, `onPatchDone()`, `onPatchUploaded()`
- Modified: `captureFrame()`, `onShutter()`, `toggleAuto()`, `updateUI()`, `drainQueue()`, `onAllUploaded()`

## Blocks
- Nothing blocks downstream tasks — independent of architecture

## OPEN Items
- Four-state outcome is chosen by USER on phone, not by OPERATOR on review browser
  - DoD says "offer to operator at capture review gate" — ambiguous if phone user or browser operator chooses
  - Current implementation: phone user chooses
  - If operator should choose: need webhook/polling mechanism to show operator's decision on phone
