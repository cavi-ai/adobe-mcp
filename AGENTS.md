# adobe-mcp: hard rules

These rules bind every agent and every change in this repository.

## Hard rules

### Versions come from git tags

- Feature and fix PRs never change a version: not `pyproject.toml`, `uv.lock` or a CHANGELOG heading.
- Add CHANGELOG lines under `## Unreleased`.
- An official release happens only when the owner calls it: bump `pyproject.toml` and `uv.lock` to that
  version and date the CHANGELOG section in a release PR, merge it, and push the tag `vX.Y.Z`.
  The tag publishes the release.
- No workflow bumps a version, tags or releases on push to main.

### Test the thing you changed

- Run the unit module(s) your change touches and `ruff check`. Do not run the full suite for a scoped change.
- CI and local gates never launch Photoshop, Illustrator, After Effects, Animate, Character Animator or
  Dimension. Native tests skip unless their `CAVI_*_NATIVE` variable is set.
- Native runs are optional and scoped to the one test the change needs. Never start a long
  multi-application run to check a change.

### Keep output tidy

- Working files go in a task's own scratch folder, never loose in the repository or its parent.
- Clean the scratch folder when the task ends.
