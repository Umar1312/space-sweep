# Space Sweep

A dependency-free macOS disk-space inspector and cleaner, in one Python file.

```bash
python3 space_sweep.py
```

Shows free space, a breakdown of removable categories, and lets you clean one category or run a one-touch cleanup.

## Categories

Xcode device support, Xcode DerivedData/Archives, iOS Simulators, developer and app caches, Android SDK extras, Flutter build output, LM Studio models, Dart/Pub data, and Apple aerial wallpapers. Locations that don't exist on your Mac show `0 B`.

- **Simulators:** pick which to delete from a list; the last used is kept by default. One-touch keeps only the last used.
- **Flutter:** finds projects (folders with a `pubspec.yaml`) up to 3 levels deep under `~/Developer`, `~/Projects`, `~/Code`, `~/dev`, `~/src` and `~/workspace`, and cleans their `build/` and `.dart_tool/`.
- Every deletion asks for confirmation: press Enter to continue, anything else cancels.
- Categories marked ⚠ are never included in one-touch cleanup.

## Config (optional)

`~/.config/space-sweep/config.json`:

```json
{
  "project_roots": ["~/work"],
  "extra_paths": ["~/Movies/exports"],
  "careful_paths": ["~/Library/Application Support/SomeApp"],
  "exclude": ["wallpapers", "local-models"]
}
```

- `project_roots`: where to look for Flutter projects (replaces the defaults).
- `extra_paths`: extra folders offered as a safe category.
- `careful_paths`: folders offered with a ⚠ and left out of one-touch (app profiles, anything with unsynced data).
- `exclude`: category keys to hide: `xcode-support`, `xcode-derived`, `simulators`, `caches`, `android`, `flutter`, `local-models`, `dart`, `wallpapers`.

## Warning

This permanently deletes files. Read each category's description before confirming.

## License

MIT
