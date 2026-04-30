## ASYNC / CONCURRENCY FOCUS

This file uses async or concurrent patterns. Elevate scrutiny on:
- Shared mutable state accessed from multiple coroutines / threads without locks
- Missing await on coroutines (silent no-op bug)
- Blocking I/O on the event loop (file ops, subprocess without asyncio wrappers)
- Task cancellation safety: are resources released in finally / __aexit__?
- Race conditions in init paths (double-checked locking, lazy singletons)
- asyncio.gather() that swallows exceptions via return_exceptions=True
