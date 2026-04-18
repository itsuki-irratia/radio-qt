# Release Checklist

1. Run full test suite:
   - `python -m pytest -q`
2. Run smoke checks for CLI and schedule export:
   - `python -m pytest tests/test_cli_smoke.py -q`
3. Validate startup recovery manually:
   - Corrupt `db.sqlite` or `settings.yaml` in a test config directory.
   - Start the app and confirm it boots, creates `*.corrupt-*` backups, and logs recovery.
4. Validate playback resilience manually:
   - Schedule a missing/unreadable local file.
   - Confirm the app does not freeze and one-shot status becomes `missed`.
5. Validate schedule export behavior:
   - Export from GUI and from incremental auto-export path.
   - Confirm generated JSON starts from current day and path mappings are applied.
6. Validate runtime logs:
   - Generate enough logs to exceed rotation threshold.
   - Confirm `runtime.log` keeps recent lines and app remains responsive.
7. Bump version/changelog (if used in your flow):
   - Update release notes with notable fixes and migrations.
8. Tag and publish:
   - Create release tag.
   - Attach artifacts/builds as needed.
9. Post-release sanity:
   - Launch the released build.
   - Verify schedule playback, metadata editing, and export endpoint JSON.
