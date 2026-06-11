# Follow-Up Prompts for Next Sessions

These are self-contained prompts that a fresh Claude session can pick up to
continue specific lines of work.

---

## Prompt 1: Revalidate Full Bucket 4 After Patch Set Updates

**Problem:** The historical post-Misc container crash was fixed by Patch #23,
and the active-session user delete and headless client DotNet handle gaps are
now patched as well. The next useful work is to re-run the full Microsoft
Bucket 4 workload and update the benchmark notes with the current pass/fail
profile.

**Goal:** Run a fresh Bucket 4 benchmark with the current image and confirm
that ERM, SCM, Misc, Workflow, SCM-Service, and SINGLESERVER all produce
results without the container becoming unhealthy.

**Suggested validation steps:**

1. Build the current image and run the full Bucket 4 script from the
   `PipelinePerformanceComparison` repo.
2. Capture the exact image tag, BC version, patch set, host, and command.
3. Check the final container health and the per-app test summaries.
4. Search the logs for unhandled `NullReferenceException`,
   `PlatformNotSupportedException`, `StackOverflowException`, and
   `Failed to create run request`.
5. Update the benchmark report and `KNOWN-LIMITATIONS.md` if a new active
   failure bucket appears.

**Constraints:**
- Keep the public test surface network-based: no `docker exec` dependency for
  test setup, execution, result reads, or coverage.
- Do not regress configurable credentials, license import, or Docker provider
  parity settings.
- Commit and push when done in both `bc-linux` (master) and
  `PipelinePerformanceComparison` (main) as relevant.

---

## Prompt 2: Revalidate Newer Insider Artifacts

**Problem:** The public container matrix now tracks the supported BC 27.x and
28.x artifact range, while Microsoft's internal comparison may use newer
insider builds. Side-by-side comparison is suggestive but not definitive when
the artifact versions differ.

**What we have:**
- Working bc-linux setup on the current supported public artifact range with
  the current startup-hook and binary patch set
- Cecil binary patches for `CodeAnalysis.dll`, `Mono.Cecil.dll`,
  `Nav.Ncl.dll`, `TestPageClient.dll`, `Nav.Types.dll`
- Download script at `scripts/download-artifacts.sh`
- Microsoft's Bucket 1: 151 min on their self-hosted runners (29.0)
- Our Bucket 1 partial: 19 min local for 6 apps on the older 27.5 baseline

**Goal:** Get a working bc-linux container on BC 29.0 (insider build) and
run the same Bucket 1 / Bucket 4 test apps so the comparison is
version-matched.

**Suggested investigation steps:**

1. Check whether 29.0 sandbox artifacts are publicly downloadable or require
   insider authentication. The MS pipeline uses
   `https://bcinsider-fvh2ekdjecfjd6gk.b02.azurefd.net/sandbox/29.0.<build>/base`
   which is the insider feed.

2. If insider auth is needed, check if Stefan has access to download
   manually and stage the artifacts somewhere the script can read.

3. Try `BC_VERSION=29.0 docker compose up -d` and see what breaks. Likely
   suspects:
   - Cecil patches may target methods that have been refactored in 29.0
   - StartupHook patches may target types/methods that have been renamed
     or moved in 29.0
   - The HttpSysStub may need updates for any new request handling

4. Re-apply each patch one by one, fixing as needed. The ALDirectCompile
   PR investigation pattern (per-method fixups) applies here too.

5. Once 29.0 boots cleanly, run the Bucket 1 benchmark on 29.0 and compare
   to Microsoft's 151 min number.

**Constraints:**
- Don't break the current supported public artifact range
- Document any 29.0-specific patches separately so we can compare effort
  needed across versions

---

## Prompt 3: Apply for ISV Partner Validation

**Goal:** Get 3-5 ISV partners running their own test apps on the bc-linux
pipeline. This validates the approach in real-world conditions and provides
adoption signal for the Microsoft pitch.

**Why this matters:** "Microsoft's own tests run faster on our setup" is one
data point. "5 ISVs already use this in their pipelines and saved X hours
per week" is a much stronger signal that the platform is production-ready.

**Targets to approach:**
- Stefan Maron community ISVs with non-trivial test suites
- BC AL community projects on GitHub that publish their own test apps
- AL ecosystem maintainers (1ClickFactory, Continia, etc.) who might be
  interested in cheaper CI

**What to give them:**
- The bc-linux Docker Compose setup
- The benchmark script as a starting point
- Documentation of known limitations (KNOWN-LIMITATIONS.md)
- A simple intake form: "what's your test suite size, what's your current CI
  cost, what would you need from us to try this?"

**Goal output:** A short doc with concrete numbers from real ISV usage that
can be added to the Microsoft pitch.
