# Preprocessor Symbols + Runtime Version Inputs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `preprocessor_symbols` and `runtime_version` inputs to `bc-test-from-source.yml` (and its example templates), with auto-derive of both `runtime` and `al_tool_version` from `bc_version`, so a multi-version matrix only needs `bc_version` + `preprocessor_symbols`.

**Architecture:** Four tightly-coupled changes to the reusable workflow: (1) two new inputs + `al_tool_version` default change, (2) auto-derive logic in the Install AL compiler step, (3) a new "Patch app.json runtime" step inserted between Stage symbols and Compile, (4) a `PP_FLAG` array injected into both `compile_dir` branches. The same pattern is then mirrored into both consumer-facing example templates and documented in README + examples/README.

**Tech Stack:** GitHub Actions YAML, Azure Pipelines YAML, Bash, Python 3 (inline heredoc for app.json patching).

---

## Files modified

| File | What changes |
|---|---|
| `.github/workflows/bc-test-from-source.yml` | New inputs; al_tool auto-derive; patch step; PP_FLAG in compile_dir |
| `examples/github-workflows/bc-test-from-source.yml` | Mirror: new env vars; auto-derive; patch step; PP_FLAG |
| `examples/azure-pipelines/bc-test-from-source.yml` | Mirror: new variables; auto-derive; patch step; PP_FLAG |
| `examples/github-workflows/README.md` | New input rows; updated al_tool_version default note |
| `README.md` | New bc_version → runtime → al_tool mapping section |

---

## Task 1: New inputs + al_tool_version default change in the reusable workflow

**Files:**
- Modify: `.github/workflows/bc-test-from-source.yml:59-63` (al_tool_version default)
- Modify: `.github/workflows/bc-test-from-source.yml:57-58` (insert new inputs before al_tool_version)

- [ ] **Step 1.1: Add preprocessor_symbols and runtime_version inputs**

  In `.github/workflows/bc-test-from-source.yml`, after the `codeunit_range` input block (line 57, after `type: string`) and before the `al_tool_version` block (line 59), insert:

  ```yaml
        preprocessor_symbols:
          description: >-
            Comma-separated preprocessor symbols passed to /preprocessorsymbols
            (e.g. "BC27PLUS,BC28PLUS"). Empty = no flag passed to the compiler.
          required: false
          type: string
          default: ""
        runtime_version:
          description: >-
            Override app.json "runtime" before compile (e.g. "16.0" for BC 27,
            "17.0" for BC 28). Auto-derived from bc_version when blank.
            Set explicitly to suppress auto-derive for edge cases.
          required: false
          type: string
          default: ""
  ```

- [ ] **Step 1.2: Change al_tool_version default to empty string**

  On line 63, change:
  ```yaml
          default: "16.2.28.57946"
  ```
  to:
  ```yaml
          default: ""
  ```

  Also update the description on line 60 to:
  ```yaml
          description: "Linux AL compiler tool version (Microsoft.Dynamics.BusinessCentral.Development.Tools.Linux NuGet). Auto-derived from bc_version when blank: BC 27 → 16.2.28.57946, BC 28 → 17.0.34.45391."
  ```

- [ ] **Step 1.3: Verify the input block looks correct**

  ```bash
  grep -A5 "preprocessor_symbols\|runtime_version\|al_tool_version" \
    .github/workflows/bc-test-from-source.yml | head -40
  ```

  Expected: three consecutive input blocks, `al_tool_version` with `default: ""`.

---

## Task 2: Auto-derive al_tool_version in the Install AL compiler step

**Files:**
- Modify: `.github/workflows/bc-test-from-source.yml` — "Install AL compiler (Linux)" step (currently lines 318-325)

- [ ] **Step 2.1: Replace the Install AL compiler step body**

  Find the step (search for `Install AL compiler (Linux)`). Replace its `run:` block with:

  ```yaml
        run: |
          bash bc-linux/scripts/workflow-summary.sh begin AL_INSTALL "Install AL compiler"
          AL_TOOL="${{ inputs.al_tool_version }}"
          BC_MAJOR=$(echo "${{ inputs.bc_version }}" | cut -d. -f1)
          if [ -z "$AL_TOOL" ]; then
            case "$BC_MAJOR" in
              27) AL_TOOL="16.2.28.57946" ;;
              28) AL_TOOL="17.0.34.45391" ;;
              *)
                echo "::warning::bc_version major $BC_MAJOR is not in the al_tool lookup table — using fallback 16.2.28.57946"
                AL_TOOL="16.2.28.57946"
                ;;
            esac
          fi
          echo "Installing AL compiler $AL_TOOL"
          dotnet tool install -g \
            Microsoft.Dynamics.BusinessCentral.Development.Tools.Linux \
            --version "$AL_TOOL" 2>/dev/null || true
          echo "$HOME/.dotnet/tools" >> "$GITHUB_PATH"
          bash bc-linux/scripts/workflow-summary.sh end AL_INSTALL
  ```

- [ ] **Step 2.2: Verify the step looks correct**

  ```bash
  grep -A25 "Install AL compiler (Linux)" .github/workflows/bc-test-from-source.yml | head -30
  ```

  Expected: case statement with 27/28/fallback, warning annotation on unknown major.

---

## Task 3: New "Patch app.json runtime" step in the reusable workflow

**Files:**
- Modify: `.github/workflows/bc-test-from-source.yml` — insert new step between "Stage symbols from BC artifacts" and "Compile AL apps (no publish yet)"

- [ ] **Step 3.1: Insert the patch step**

  After the "Stage symbols from BC artifacts" step (ends with `bash bc-linux/scripts/workflow-summary.sh end SYMBOLS`), insert a new step before "Compile AL apps (no publish yet)":

  ```yaml
        - name: Patch app.json runtime
          env:
            APP_DIRS: ${{ inputs.app_dirs }}
            TEST_APP_DIRS: ${{ inputs.test_app_dirs }}
            RUNTIME_VERSION: ${{ inputs.runtime_version }}
          run: |
            EFFECTIVE_RUNTIME="$RUNTIME_VERSION"
            BC_MAJOR=$(echo "${{ inputs.bc_version }}" | cut -d. -f1)
            if [ -z "$EFFECTIVE_RUNTIME" ]; then
              case "$BC_MAJOR" in
                27) EFFECTIVE_RUNTIME="16.0" ;;
                28) EFFECTIVE_RUNTIME="17.0" ;;
                *)
                  echo "::warning::bc_version major $BC_MAJOR is not in the runtime lookup table — using fallback 14.0"
                  EFFECTIVE_RUNTIME="14.0"
                  ;;
              esac
            fi
            echo "Patching app.json runtime → $EFFECTIVE_RUNTIME"
            for d in $APP_DIRS $TEST_APP_DIRS; do
              [ -z "$d" ] && continue
              F="project/$d/app.json"
              [ -f "$F" ] || continue
              python3 - "$F" "$EFFECTIVE_RUNTIME" <<'PYEOF'
  import sys, json, pathlib
  p = pathlib.Path(sys.argv[1])
  d = json.loads(p.read_text())
  if d.get("runtime") != sys.argv[2]:
      d["runtime"] = sys.argv[2]
      p.write_text(json.dumps(d, indent=2) + "\n")
      print(f"  patched {p}: runtime → {sys.argv[2]}")
  else:
      print(f"  {p}: runtime already {sys.argv[2]}, skip")
  PYEOF
            done
  ```

- [ ] **Step 3.2: Verify step order**

  ```bash
  grep -n "Stage symbols\|Patch app.json\|Compile AL apps" .github/workflows/bc-test-from-source.yml
  ```

  Expected output (line numbers will vary): Stage symbols → Patch app.json runtime → Compile AL apps, in that order.

---

## Task 4: PP_FLAG + PREPROCESSOR_SYMBOLS in the reusable workflow compile step

**Files:**
- Modify: `.github/workflows/bc-test-from-source.yml` — "Compile AL apps (no publish yet)" step

- [ ] **Step 4.1: Add PREPROCESSOR_SYMBOLS to the compile step's env block**

  In the `env:` block of "Compile AL apps (no publish yet)" (currently ends at `ENABLE_CODE_ANALYZERS_ON_TEST_APPS`), add:

  ```yaml
              PREPROCESSOR_SYMBOLS: ${{ inputs.preprocessor_symbols }}
  ```

- [ ] **Step 4.2: Add PP_FLAG derivation before compile_dir**

  In the `run:` block, immediately before the line `compile_dir() {`, insert:

  ```bash
                PP_FLAG=()
                [ -n "$PREPROCESSOR_SYMBOLS" ] && PP_FLAG=("/preprocessorsymbols:$PREPROCESSOR_SYMBOLS")
  ```

- [ ] **Step 4.3: Inject PP_FLAG into the analyzer-capture branch of compile_dir**

  In compile_dir, find the line:
  ```bash
              AL compile "/project:$src" "/packagecachepath:$src/.alpackages" "/out:$out" "$@" 2>&1 | tee "$tmplog"
  ```
  Replace with:
  ```bash
              AL compile "/project:$src" "/packagecachepath:$src/.alpackages" "/out:$out" "${PP_FLAG[@]}" "$@" 2>&1 | tee "$tmplog"
  ```

- [ ] **Step 4.4: Inject PP_FLAG into the plain (no-analyzer) branch of compile_dir**

  In compile_dir, find the line:
  ```bash
              AL compile "/project:$src" "/packagecachepath:$src/.alpackages" "/out:$out"
  ```
  Replace with:
  ```bash
              AL compile "/project:$src" "/packagecachepath:$src/.alpackages" "/out:$out" "${PP_FLAG[@]}"
  ```

- [ ] **Step 4.5: Verify PP_FLAG appears in both branches**

  ```bash
  grep -n "PP_FLAG\|preprocessorsymbols\|PREPROCESSOR_SYMBOLS" .github/workflows/bc-test-from-source.yml
  ```

  Expected: 5 lines — one env var assignment, one derivation, one in each compile_dir branch, one in the env block.

- [ ] **Step 4.6: Commit the reusable workflow changes**

  ```bash
  git add .github/workflows/bc-test-from-source.yml
  git commit -m "feat(workflow): preprocessor_symbols + runtime_version inputs, al_tool auto-derive"
  ```

---

## Task 5: Update the GitHub Actions example template

**Files:**
- Modify: `examples/github-workflows/bc-test-from-source.yml`

The example template is a standalone (non-reusable) workflow that inlines the same compile logic. It needs the same four changes: updated AL_TOOL_VERSION env var + auto-derive, new RUNTIME_VERSION + PREPROCESSOR_SYMBOLS env vars, patch step, PP_FLAG in compile_dir.

- [ ] **Step 5.1: Update the AL_TOOL_VERSION env var and comment**

  Find (around line 67-71):
  ```yaml
    # AL compiler version. Has to match the major BC version of the
    # platform you're targeting. The version below is known to work with
    # BC 27.x. For BC 28.x bump it via:
    #   dotnet tool search Microsoft.Dynamics.BusinessCentral.Development.Tools.Linux
    AL_TOOL_VERSION: "16.2.28.57946"
  ```
  Replace with:
  ```yaml
    # AL compiler tool version. Leave blank to auto-derive from BC_VERSION
    # (recommended): BC 27.x → 16.2.28.57946, BC 28.x → 17.0.34.45391.
    # Set explicitly to pin a specific version.
    AL_TOOL_VERSION: ""
  ```

- [ ] **Step 5.2: Add RUNTIME_VERSION and PREPROCESSOR_SYMBOLS env vars**

  After the `AL_TOOL_VERSION:` line, add:
  ```yaml
    # AL runtime version written into all app.json files before compile.
    # Auto-derived from BC_VERSION when blank: BC 27 → 16.0, BC 28 → 17.0.
    RUNTIME_VERSION: ""

    # Comma-separated preprocessor symbols passed to /preprocessorsymbols.
    # Example: "BC27PLUS,BC28PLUS" for a version-gated matrix leg.
    PREPROCESSOR_SYMBOLS: ""
  ```

- [ ] **Step 5.3: Add auto-derive logic to Install AL compiler step**

  Find the "Install AL compiler (Linux)" step's run block (around line 220-225):
  ```yaml
          bash bc-linux/scripts/workflow-summary.sh begin AL_INSTALL "Install AL compiler"
          dotnet tool install -g \
            Microsoft.Dynamics.BusinessCentral.Development.Tools.Linux \
            --version "$AL_TOOL_VERSION" 2>/dev/null || true
          echo "$HOME/.dotnet/tools" >> "$GITHUB_PATH"
          bash bc-linux/scripts/workflow-summary.sh end AL_INSTALL
  ```
  Replace with:
  ```yaml
          bash bc-linux/scripts/workflow-summary.sh begin AL_INSTALL "Install AL compiler"
          TOOL="$AL_TOOL_VERSION"
          BC_MAJOR=$(echo "$BC_VERSION" | cut -d. -f1)
          if [ -z "$TOOL" ]; then
            case "$BC_MAJOR" in
              27) TOOL="16.2.28.57946" ;;
              28) TOOL="17.0.34.45391" ;;
              *)
                echo "::warning::BC_VERSION major $BC_MAJOR not in al_tool table — using fallback 16.2.28.57946"
                TOOL="16.2.28.57946"
                ;;
            esac
          fi
          echo "Installing AL compiler $TOOL"
          dotnet tool install -g \
            Microsoft.Dynamics.BusinessCentral.Development.Tools.Linux \
            --version "$TOOL" 2>/dev/null || true
          echo "$HOME/.dotnet/tools" >> "$GITHUB_PATH"
          bash bc-linux/scripts/workflow-summary.sh end AL_INSTALL
  ```

- [ ] **Step 5.4: Insert "Patch app.json runtime" step before "Compile AL apps"**

  Find the "Stage symbols from BC artifacts" step (ends around line 238 with `bash bc-linux/scripts/workflow-summary.sh end SYMBOLS`). After it and before "Compile AL apps (no publish yet)", insert:

  ```yaml
        - name: Patch app.json runtime
          run: |
            EFFECTIVE_RUNTIME="$RUNTIME_VERSION"
            BC_MAJOR=$(echo "$BC_VERSION" | cut -d. -f1)
            if [ -z "$EFFECTIVE_RUNTIME" ]; then
              case "$BC_MAJOR" in
                27) EFFECTIVE_RUNTIME="16.0" ;;
                28) EFFECTIVE_RUNTIME="17.0" ;;
                *)
                  echo "::warning::BC_VERSION major $BC_MAJOR not in runtime table — using fallback 14.0"
                  EFFECTIVE_RUNTIME="14.0"
                  ;;
              esac
            fi
            echo "Patching app.json runtime → $EFFECTIVE_RUNTIME"
            for d in $APP_DIRS $TEST_APP_DIRS; do
              [ -z "$d" ] && continue
              F="project/$d/app.json"
              [ -f "$F" ] || continue
              python3 - "$F" "$EFFECTIVE_RUNTIME" <<'PYEOF'
  import sys, json, pathlib
  p = pathlib.Path(sys.argv[1])
  d = json.loads(p.read_text())
  if d.get("runtime") != sys.argv[2]:
      d["runtime"] = sys.argv[2]
      p.write_text(json.dumps(d, indent=2) + "\n")
      print(f"  patched {p}: runtime → {sys.argv[2]}")
  else:
      print(f"  {p}: runtime already {sys.argv[2]}, skip")
  PYEOF
            done
  ```

  Note: the example template uses `$APP_DIRS` / `$TEST_APP_DIRS` / `$BC_VERSION` / `$RUNTIME_VERSION` as plain env vars (from the `env:` block at the top), not `${{ inputs.* }}` expressions.

- [ ] **Step 5.5: Add PP_FLAG to compile_dir in the example template**

  In the "Compile AL apps (no publish yet)" step's compile_dir function (around line 292), immediately before `compile_dir() {`, add:

  ```bash
                PP_FLAG=()
                [ -n "$PREPROCESSOR_SYMBOLS" ] && PP_FLAG=("/preprocessorsymbols:$PREPROCESSOR_SYMBOLS")
  ```

  Then inject into both AL compile invocations (same as Task 4, steps 4.3 and 4.4, but inside this file).

- [ ] **Step 5.6: Verify**

  ```bash
  grep -n "PP_FLAG\|PREPROCESSOR_SYMBOLS\|RUNTIME_VERSION\|AL_TOOL_VERSION\|Patch app.json\|auto-derive\|fallback" \
    examples/github-workflows/bc-test-from-source.yml
  ```

  Expected: at least one hit for each keyword.

- [ ] **Step 5.7: Commit**

  ```bash
  git add examples/github-workflows/bc-test-from-source.yml
  git commit -m "feat(examples/github): mirror preprocessor_symbols + runtime auto-derive"
  ```

---

## Task 6: Update the Azure Pipelines example template

**Files:**
- Modify: `examples/azure-pipelines/bc-test-from-source.yml`

The AzDO template uses `$(VAR_NAME)` syntax for variable expansion and `##vso[task.prependpath]` instead of `$GITHUB_PATH`. Otherwise the logic is identical.

- [ ] **Step 6.1: Update AL_TOOL_VERSION variable and comment**

  Find (around line 65-67):
  ```yaml
    # AL compiler version. Has to match the major BC version. The version
    # below is known to work with BC 27.x. For BC 28.x bump it.
    AL_TOOL_VERSION: '16.2.28.57946'
  ```
  Replace with:
  ```yaml
    # AL compiler tool version. Leave blank to auto-derive from BC_VERSION
    # (recommended): BC 27.x → 16.2.28.57946, BC 28.x → 17.0.34.45391.
    # Set explicitly to pin a specific version.
    AL_TOOL_VERSION: ''
  ```

- [ ] **Step 6.2: Add RUNTIME_VERSION and PREPROCESSOR_SYMBOLS variables**

  After the `AL_TOOL_VERSION:` line, add:
  ```yaml
    # AL runtime version written into all app.json files before compile.
    # Auto-derived from BC_VERSION when blank: BC 27 → 16.0, BC 28 → 17.0.
    RUNTIME_VERSION: ''

    # Comma-separated preprocessor symbols passed to /preprocessorsymbols.
    # Example: 'BC27PLUS,BC28PLUS' for a version-gated matrix leg.
    PREPROCESSOR_SYMBOLS: ''
  ```

- [ ] **Step 6.3: Add auto-derive logic to the Install AL compiler bash step**

  Find the bash step at around line 207-214 (displayName: 'Install Linux AL compiler'). Its current run body:
  ```bash
      bash "$(Agent.BuildDirectory)/bc-linux/scripts/workflow-summary.sh" begin AL_INSTALL "Install AL compiler"
      dotnet tool install -g \
        Microsoft.Dynamics.BusinessCentral.Development.Tools.Linux \
        --version "$(AL_TOOL_VERSION)" 2>/dev/null || true
      echo "##vso[task.prependpath]$HOME/.dotnet/tools"
      bash "$(Agent.BuildDirectory)/bc-linux/scripts/workflow-summary.sh" end AL_INSTALL
  ```
  Replace with:
  ```bash
      bash "$(Agent.BuildDirectory)/bc-linux/scripts/workflow-summary.sh" begin AL_INSTALL "Install AL compiler"
      TOOL="$(AL_TOOL_VERSION)"
      BC_MAJOR=$(echo "$(BC_VERSION)" | cut -d. -f1)
      if [ -z "$TOOL" ]; then
        case "$BC_MAJOR" in
          27) TOOL="16.2.28.57946" ;;
          28) TOOL="17.0.34.45391" ;;
          *)
            echo "##vso[task.logissue type=warning]BC_VERSION major $BC_MAJOR not in al_tool table — using fallback 16.2.28.57946"
            TOOL="16.2.28.57946"
            ;;
        esac
      fi
      echo "Installing AL compiler $TOOL"
      dotnet tool install -g \
        Microsoft.Dynamics.BusinessCentral.Development.Tools.Linux \
        --version "$TOOL" 2>/dev/null || true
      echo "##vso[task.prependpath]$HOME/.dotnet/tools"
      bash "$(Agent.BuildDirectory)/bc-linux/scripts/workflow-summary.sh" end AL_INSTALL
  ```

  Note: Azure Pipelines uses `##vso[task.logissue type=warning]` instead of `::warning::` for annotation-style warnings.

- [ ] **Step 6.4: Insert "Patch app.json runtime" bash step before "Compile AL apps"**

  Find `displayName: 'Stage symbols from BC artifacts'`. After that step and before `displayName: 'Compile AL apps (no publish yet)'`, insert a new bash step:

  ```yaml
    - bash: |
        EFFECTIVE_RUNTIME="$(RUNTIME_VERSION)"
        BC_MAJOR=$(echo "$(BC_VERSION)" | cut -d. -f1)
        if [ -z "$EFFECTIVE_RUNTIME" ]; then
          case "$BC_MAJOR" in
            27) EFFECTIVE_RUNTIME="16.0" ;;
            28) EFFECTIVE_RUNTIME="17.0" ;;
            *)
              echo "##vso[task.logissue type=warning]BC_VERSION major $BC_MAJOR not in runtime table — using fallback 14.0"
              EFFECTIVE_RUNTIME="14.0"
              ;;
          esac
        fi
        echo "Patching app.json runtime → $EFFECTIVE_RUNTIME"
        for d in $(APP_DIRS) $(TEST_APP_DIRS); do
          [ -z "$d" ] && continue
          F="$(Pipeline.Workspace)/project/$d/app.json"
          [ -f "$F" ] || continue
          python3 - "$F" "$EFFECTIVE_RUNTIME" <<'PYEOF'
  import sys, json, pathlib
  p = pathlib.Path(sys.argv[1])
  d = json.loads(p.read_text())
  if d.get("runtime") != sys.argv[2]:
      d["runtime"] = sys.argv[2]
      p.write_text(json.dumps(d, indent=2) + "\n")
      print(f"  patched {p}: runtime → {sys.argv[2]}")
  else:
      print(f"  {p}: runtime already {sys.argv[2]}, skip")
  PYEOF
        done
      displayName: 'Patch app.json runtime'
  ```

  Note: AzDO uses `$(Pipeline.Workspace)/project/$d/app.json` because the consumer checkout is in `$(Pipeline.Workspace)/project/`. The GitHub example uses `project/$d/app.json` relative to the runner workspace root.

- [ ] **Step 6.5: Add PP_FLAG to compile_dir in the AzDO template**

  In the 'Compile AL apps (no publish yet)' bash step, immediately before `compile_dir() {`, add:

  ```bash
        PP_FLAG=()
        [ -n "$(PREPROCESSOR_SYMBOLS)" ] && PP_FLAG=("/preprocessorsymbols:$(PREPROCESSOR_SYMBOLS)")
  ```

  Then inject into both AL compile invocations inside compile_dir (same pattern as Task 4 steps 4.3 and 4.4).

- [ ] **Step 6.6: Verify**

  ```bash
  grep -n "PP_FLAG\|PREPROCESSOR_SYMBOLS\|RUNTIME_VERSION\|AL_TOOL_VERSION\|Patch app.json\|fallback\|logissue" \
    examples/azure-pipelines/bc-test-from-source.yml
  ```

  Expected: hits for each keyword.

- [ ] **Step 6.7: Commit**

  ```bash
  git add examples/azure-pipelines/bc-test-from-source.yml
  git commit -m "feat(examples/azdo): mirror preprocessor_symbols + runtime auto-derive"
  ```

---

## Task 7: Update examples/github-workflows/README.md

**Files:**
- Modify: `examples/github-workflows/README.md:52` (al_tool_version row) and nearby

- [ ] **Step 7.1: Update al_tool_version row default value**

  Find line 52:
  ```markdown
  | `al_tool_version` | no | `16.2.28.57946` | Linux AL compiler tool version |
  ```
  Replace with:
  ```markdown
  | `al_tool_version` | no | *(auto-derived from bc_version)* | Linux AL compiler NuGet version. Auto-derived: BC 27 → `16.2.28.57946`, BC 28 → `17.0.34.45391`. Set explicitly to pin. |
  ```

- [ ] **Step 7.2: Add preprocessor_symbols and runtime_version rows**

  After the `al_tool_version` row and before the `runner_image` row, add:
  ```markdown
  | `preprocessor_symbols` | no | `""` | Comma-separated preprocessor symbols for `/preprocessorsymbols` (e.g. `"BC27PLUS,BC28PLUS"`). |
  | `runtime_version` | no | *(auto-derived from bc_version)* | Override `app.json` `runtime` before compile. Auto-derived when blank: BC 27 → `16.0`, BC 28 → `17.0`. |
  ```

- [ ] **Step 7.3: Verify table**

  ```bash
  grep -A2 "al_tool_version\|preprocessor_symbols\|runtime_version" examples/github-workflows/README.md
  ```

  Expected: all three rows present with the new default text.

- [ ] **Step 7.4: Commit**

  ```bash
  git add examples/github-workflows/README.md
  git commit -m "docs(examples): add preprocessor_symbols + runtime_version to input table"
  ```

---

## Task 8: Update README.md — bc_version → al_tool + runtime mapping section

**Files:**
- Modify: `README.md` — around line 50-66 (the AL compiler tool install section)

- [ ] **Step 8.1: Replace the AL tool install snippet with an expanded section**

  Find the current section (around line 50-65):
  ```markdown
  **Optional — only if you want to compile AL projects from the command line**
  without using the VS Code AL extension's F5 build:

  - `.NET 8 SDK` plus the Linux AL compiler tool:

    ```bash
    dotnet tool install -g \
      Microsoft.Dynamics.BusinessCentral.Development.Tools.Linux \
      --version 16.2.28.57946
    echo 'export PATH="$HOME/.dotnet/tools:$PATH"' >> ~/.bashrc
    ```

    (Bump the version for newer BC majors. The version above is known to
    work with BC 27.x. If you use VS Code with the AL Language extension,
    F5 / Ctrl+F5 publishes via the dev endpoint without ever needing the
    CLI compiler — skip this section.)
  ```

  Replace with:
  ```markdown
  **Optional — only if you want to compile AL projects from the command line**
  without using the VS Code AL extension's F5 build:

  - `.NET 8 SDK` plus the Linux AL compiler tool. Pick the version that matches your BC major:

    | BC version | AL runtime (`app.json`) | `al_tool_version` (NuGet) |
    |---|---|---|
    | BC 27.x | `16.0` | `16.2.28.57946` |
    | BC 28.x | `17.0` | `17.0.34.45391` |

    ```bash
    # BC 27.x
    dotnet tool install -g \
      Microsoft.Dynamics.BusinessCentral.Development.Tools.Linux \
      --version 16.2.28.57946
    echo 'export PATH="$HOME/.dotnet/tools:$PATH"' >> ~/.bashrc
    ```

    The reusable workflow (`bc-test-from-source.yml`) auto-derives both values
    from `bc_version` — you only need to set `al_tool_version` explicitly to
    pin a specific build. If you use VS Code with the AL Language extension,
    F5 / Ctrl+F5 publishes via the dev endpoint without the CLI compiler —
    skip this section.

    **Why does the smoke test use `runtime: "14.0"`?**
    `extensions/smoke-test/app.json` uses a deliberately low runtime value so
    the same committed file compiles cleanly against any supported BC version
    without patching. Consumer apps should use the runtime matching their
    minimum supported BC version (auto-derived by the workflow).
  ```

- [ ] **Step 8.2: Verify the new section renders correctly**

  ```bash
  grep -A30 "Optional — only if you want to compile" README.md | head -35
  ```

  Expected: table with BC 27/28 rows, code block, explanation paragraph, smoke-test note.

- [ ] **Step 8.3: Commit**

  ```bash
  git add README.md
  git commit -m "docs: add bc_version → al_tool + runtime mapping table, explain smoke test runtime"
  ```

---

## Self-review checklist

- [x] **Spec coverage:**
  - `preprocessor_symbols` input + `/preprocessorsymbols` flag in compile_dir: Tasks 1, 4, 5, 6 ✓
  - `runtime_version` input + patch step: Tasks 1, 3, 5, 6 ✓
  - `al_tool_version` default → `""` + auto-derive: Tasks 1, 2, 5, 6 ✓
  - Warning on unknown bc_version major (both lookup tables): Tasks 2, 3, 5, 6 ✓
  - Patch scope: `project/` only, smoke test untouched: Task 3 ✓
  - `examples/azure-pipelines/bc-test-from-source.yml` patched: Task 6 ✓
  - AzDO matrix example NOT added (out of scope): not present ✓
  - `examples/github-workflows/README.md` updated: Task 7 ✓
  - `README.md` mapping section + smoke test explanation: Task 8 ✓

- [x] **No placeholders:** All steps contain complete code.

- [x] **PP_FLAG consistency:** Defined as empty array; both compile_dir branches inject `"${PP_FLAG[@]}"` before `"$@"` (so symbols come before analyzer flags). Named identically across all tasks.

- [x] **AzDO warning syntax:** `##vso[task.logissue type=warning]` (Tasks 6.3, 6.4) vs GitHub's `::warning::` (Tasks 2, 3) — correct per platform.

- [x] **AzDO path prefix:** `$(Pipeline.Workspace)/project/$d/app.json` in Task 6.4 vs `project/$d/app.json` in Tasks 3 and 5.4 — matches each template's checkout path conventions.
