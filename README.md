# Space Sweep

A dependency-free macOS disk-space inspector and cleaner, in one Python file.

```bash
python3 space_sweep.py
```

Shows free space, a breakdown of removable categories (Xcode data, simulators, caches, Flutter build output, Android SDK extras, local models, wallpapers, app profiles), and lets you clean one category or run a one-touch cleanup.

- Every deletion needs a typed confirmation (`CLEAN`, or `DELETE PROFILE DATA` for app profiles).
- App profiles are never included in one-touch cleanup.
- Paths are hardcoded for one machine (e.g. `~/Developer/Mobile`); edit `categories()` to fit yours.
