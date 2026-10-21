## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: root `GLOSSARY.md` + `docs/adr/`. See `docs/agents/domain.md`.

### Native deps

`scripts/setup-native-deps.sh` provisions snappy + LevelDB + `plyvel`; run it on
a fresh machine, or after a Python/architecture change.