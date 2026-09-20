# Contributing to backuprig

Thanks for your interest! backuprig keeps a strict separation between pure
logic (unit-testable, no network) and I/O (SSH sessions, Qt), so contributing
a new vendor or feature should never require a real device to test against.

## Architecture in one paragraph

`backuprig/adapters/*.py` each implement `VendorAdapter.fetch(conn, exec_fn)`
where `exec_fn(command, timeout) -> str` is injected by the caller - adapters
never open a socket themselves. `backuprig/transport.py` is the only module
that talks real SSH (via paramiko). `backuprig/engine.py` wires a `Device`
to the right adapter and transport. `backuprig/store.py` persists devices
and backup history in an encrypted SQLite vault (`backuprig/crypto.py`).
`backuprig/cli.py` and `backuprig/gui/app.py` are two independent front ends
over the same store/engine.

## The rules

1. **New vendor adapters** go in `backuprig/adapters/`, subclass
   `VendorAdapter`, and are pure functions of `(ConnectionParams, exec_fn)`.
   No `socket`/`paramiko` imports inside an adapter - that's what makes them
   testable with a fake `exec_fn`.
2. **Every adapter needs offline tests** in `tests/test_adapters.py` using a
   fake `exec_fn` dict/callable. If you can, also verify your parsing logic
   against real command output you've captured from a lab device (redact
   secrets first).
3. Register new adapters in `backuprig/adapters/__init__.py`'s `ADAPTERS`
   dict - the GUI's vendor dropdown and the CLI's `vendors` command both
   read from there automatically.
4. **GUI changes** need a smoke test in `tests/test_gui.py` (run headless
   with `QT_QPA_PLATFORM=offscreen`). Avoid triggering real blocking modals
   in tests - stub `QMessageBox` methods as needed (see existing tests for
   the pattern).
5. Never log, print, or store a master password or a decrypted device
   secret outside of the encrypted vault.
6. backuprig is **read-only against devices** by design (v0.1). If you want
   to propose restore/push functionality, open an issue to discuss the
   safety model first - this is a deliberate scope decision, not an
   oversight.

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev-gui]"   # core + GUI dev deps
pytest -q                     # everything, including GUI (needs Qt libs installed)

# Core-only, no Qt system libraries required:
pip install -e ".[dev]"
pytest -q --ignore=tests/test_gui.py
```

To manually try a new adapter against a real device:

```bash
backuprig add test-device <vendor> <host> -u <user>
backuprig backup 1
backuprig export 1 out.txt && cat out.txt
```

## Pull requests

* Small, focused PRs are easiest to review.
* Include the offline tests for any new logic.
* If you're adding a vendor adapter, a short paragraph in the README's
  "Vendor notes" section describing any setup quirks (like Kerio needing SSH
  enabled manually) is very much appreciated.

Thanks! 🙏
