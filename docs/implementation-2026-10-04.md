# Implementation and acceptance boundary — 2026-10-04

The approved startup/voice visibility, single-source deployment, and media-upload
work has been implemented and merged locally. No GitHub push was performed.

## Verification

- Framework: 83/83 tests pass, including real loopback HTTP/TCP/Unix sockets,
  CPU fixture process lifecycle, installed headless Chromium at mobile/desktop
  widths, and native browser XHR/fetch against the actual Mock UI backend.
- Nano deployment: 119/119 tests pass after reversible directory migration,
  including ASR CLI/UI-mode with stub audio/recognizers and official profile/API.
- Migration temporary-tree apply/rollback/failure tests: 10/10 pass.
- No real model, microphone, vision decoder or extra inference process was loaded.
- Compile, whitespace and tracked-file safety scans pass. Real profiles, uploads,
  weights, virtual environments and local archives are not tracked.

One independent read-only review found five issues. Each was reproduced and
fixed in one bounded pass: SSE shutdown draining; media lease/reset admission;
voice capture/media exclusion; failed Continue acknowledgement reservation;
write-ahead migration recovery. The final suites ran after these fixes.

## Framework behavior

Text and recognized voice transcripts keep their shared text dispatch path.
Image/video use typed MediaRequest and explicit adapter registration. They do not
give a text-only model visual capabilities. mock-media identifies itself and
states that it has not analyzed the content. See [media integration](media-integration.md).

Raw uploads require a unique fixed Content-Length, bounded chunk reads, MIME/header
matching, quota/count/TTL and a total deadline. Valid Expect:100-continue requests
receive acknowledgement only after preflight. Failed acknowledgement releases its
reservation. JSON POST connections close, so rejected unread bodies cannot become
another request. SSE remains open during operation but exits during normal shutdown.

Media admission coordinates with voice capture, reset and model switching before
acquiring a file lease. A stamp includes backend, server epoch, lifecycle revision
and conversation revision. Reset succeeds before frontend messages are cleared;
failed reset preserves records. Leases survive expiry/deletion while a model is
using the file and are reclaimed when its adapter returns.

The deployed UI links to this canonical repository; deployment entrypoints supply
site-specific assistant/backend/log paths and model installation root. ASR and
inference repositories/environments remain separate; no model weights are copied.
Historical files are archived, not deleted, with a recoverable local journal.

## Not hardware acceptance

The Nano startup window is configurable at 180 seconds only in its deployment
profile; generic defaults remain unchanged. Stage/attempt logging improves evidence
but does not prove cold-start duration or solve every startup failure.

Actual Qwen startup/generation/shutdown and GPU/RAM/SWAP behavior, real microphone
and ASR device operation, and any third-party visual model must still be tested by
the deployment operator. Header filtering is not full validation or safe decoding.
Visual adapters must impose their own pixel/frame/duration/decoder budgets.

This is an unauthenticated trusted-local/LAN demo, not a public Internet service.
No TTS, token streaming, multiuser sessions, browser microphone capture or second
large model preload was introduced. No dependencies or inference libraries changed.
