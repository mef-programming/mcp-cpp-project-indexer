# Maintenance Checklist

Use this checklist after every behavior, tool, prompt, or documentation change.

## 1. Scope

- [ ] The change is narrowly scoped to the requested behavior.
- [ ] No unrelated refactors or generated/cache files are included.
- [ ] Existing user/local changes were not reverted.

## 2. Tool Surface

- [ ] Tool descriptions still match actual behavior.
- [ ] Tool group descriptions are still correct.
- [ ] New or changed arguments are documented in the tool schema.
- [ ] Defaults, limits, compact flags, and response-format flags are consistent.
- [ ] "Does not" boundaries are still explicit for metadata-only tools.
- [ ] Legacy aliases or terminology were removed or intentionally kept.

## 3. Prompt Template

- [ ] `prompt_template.md` reflects new or changed tools.
- [ ] The recommended workflow still points to the best first tool.
- [ ] Compact/default usage rules are updated when arguments change.
- [ ] Evidence rules still distinguish metadata, source, diagnostics, and changes.
- [ ] Any renamed concept is updated everywhere in the prompt.

## 4. README

- [ ] `readme.md` reflects new behavior, commands, and workflow guidance.
- [ ] The command-line reference is updated for new/changed switches.
- [ ] Tool overview entries are updated for new/changed tools or arguments.
- [ ] Example workflows still use valid tool names and current terminology.
- [ ] The table of contents links still point to existing headings.

## 5. CLI And Menu

- [ ] `build_project_index.py`, `update_project_index.py`, and watcher flags stay aligned.
- [ ] `indexer_menu.py` exposes relevant new options or renamed switches.
- [ ] Help text matches the actual command-line behavior.
- [ ] Progress output remains single-line where expected.

## 6. Index Data Compatibility

- [ ] Manifest/schema/count fields are updated if persisted data changed.
- [ ] Full builds write all data needed by the first incremental update.
- [ ] Incremental update and watcher paths handle the new data.
- [ ] Cache reload behavior remains explicit and does not rebuild the index.

## 7. Tests And Smoke Checks

- [ ] Python syntax check passes for changed `.py` files.
- [ ] Targeted smoke test covers the changed behavior.
- [ ] Full build still works on a representative project when relevant.
- [ ] Incremental update still works when relevant.
- [ ] Watcher behavior still works when relevant.
- [ ] MCP tool call smoke test was run for changed tool behavior.

## 8. Output Quality

- [ ] JSON output is stable and bounded.
- [ ] Compact output does not remove required routing evidence.
- [ ] Source line numbers and source text are not reformatted.
- [ ] Diagnostics wording cannot be confused with compiler/runtime debugging.
- [ ] Large responses set truncation flags where appropriate.

## 9. Relay Compatibility

- [ ] Relay/tool-routing assumptions remain valid.
- [ ] Renamed arguments or concepts are reflected in relay defaults if needed.
- [ ] Tool descriptions are clear enough for the local tool governor.
- [ ] Test sequences should be updated when workflows change.

## 10. Commit Prep

- [ ] `git status --short` was reviewed.
- [ ] Only intended source/docs files are staged.
- [ ] Cache files such as `__pycache__`, `.vs`, and generated project indexes are excluded.
- [ ] Commit message is concise, imperative, and matches the actual change.

