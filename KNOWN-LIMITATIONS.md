# Known Business Central Container Test Limitations

## ~~"User cannot be deleted because logged on" (~142 failures in SINGLESERVER)~~ (FIXED)

**Status**: Fixed by the `userdelete` Nav.Ncl patch applied during startup.
The patch skips the active-session throw in
`SystemTableTriggers.OnBeforeDeleteAsync` and then continues with the remaining
user-delete validation.

**Root cause**: Microsoft's test cleanup code does broad `User.DeleteAll()` or
`User.FindFirst(); User.Delete()` without filtering out the session user. BC's
platform rejects the delete before it even reaches the transaction layer, so
codeunit isolation rollback can't help.

**Biggest contributors**:
- `DocumentApprovalUsers.TestCleanup()` — calls `DeleteAllUsers()` which deletes
  `FindFirst()` result (60+ calls)
- `UserCardTest.EnsureNoUsers()` — `User.DeleteAll()` unfiltered
- `UserAccessinSaaSTests.Initialize()` — `User.DeleteAll(true)` unfiltered
- `DocumentApprovalDocuments` teardown — explicitly targets `UserId()` for cleanup

**Historical platform difference**: Microsoft containers usually authenticate
through an OS identity that is separate from the BC User table, while this image
uses the configured NavUserPassword user for the network surface. Before the
`userdelete` patch, Microsoft cleanup code could hit the active-session guard
when deleting the current BC user. The patched image skips only that guard so
the test cleanup path behaves like the standard container test surface.

**Impact on benchmarks (historical)**: These failures happened during
setup/teardown, not during the actual test logic. Tests that failed early ran
faster than they would on Windows, slightly skewing timing comparisons for
affected codeunits.

## ~~"NullReferenceException in NSClientCallback.CreateDotNetHandle" (~29+ failures)~~ (FIXED)

**Status**: Fixed by StartupHook Patch #24. Headless client-side DotNet handle
creation now returns a dummy `NavAutomationHandle` instead of aborting the
session when no UI client exists.

**Root cause**: Tests that use .NET controls requiring a UI context (Camera,
Barcode Scanner, etc.) crash because the headless test runner has no client UI
to create .NET control handles on. `NSClientCallback.CreateDotNetHandle` throws
NullReferenceException when there's no UI session.

**Example**: `Camera Page Impl.` (CU 1908) `.IsAvailable` → crashes any test
that opens a page with a Camera control.

**Fix**: Patch `NSClientCallback.CreateDotNetHandle` in Nav.Service and
`HeadlessClientCallback.CreateDotNetHandle` in Nav.Ncl to return a dummy
automation handle. This turns crashes into graceful no-ops where the DotNet
control is unavailable.

## ~~Container crash after Tests-Misc in sequential Bucket 4 runs~~ (FIXED — Patch #23)

**Status**: Fixed in Patch #23 (`OfficeWordDocumentPictureMerger.ReplaceMissingImageWithTransparentImage`).

**Symptom (was)**: When running Bucket 4 sequentially
(ERM → SCM → Misc → Workflow → SCM-Service → SINGLESERVER), the BC container
became unhealthy after Tests-Misc completed. The remaining 3 apps (Workflow,
SCM-Service, SINGLESERVER) all failed with "Failed to create run request"
because the API was dead.

**Root cause**: Infinite recursion in Microsoft's
`Microsoft.Dynamics.Nav.OpenXml.Word.DocumentMerger.OfficeWordDocumentPictureMerger.ReplaceMissingImageWithTransparentImage`.
When a Word report references a missing image, the method calls
`MergePictureElements` with the transparent placeholder, which re-enters
`ReplaceMissingImageWithTransparentImage` unconditionally → ~37,390 frames
deep → stack overflow → fatal session crash → container goes unhealthy.
Triggered by `TestSendToEMailAndPDFVendor` in Tests-Misc; two earlier
`NavNCLStackOverflowException` events were also visible during ERM and SCM but
were recoverable until the deeper Misc invocation killed the worker.

**Fix**: Patch #23 in `StartupHook.cs` no-ops
`ReplaceMissingImageWithTransparentImage` via JMP hook (the type is in
`Microsoft.Dynamics.Nav.OpenXml.dll`, JIT-compiled BC code → patchable).
The missing image XElement is left in place — reports render with a broken
image marker but the session survives and report generation completes.
The Misc tests do not validate rendered image content.

**Diagnostic logs (historical)**:
- Local benchmark run 2026-04-04 stack trace:
  `PipelinePerformanceComparison/benchmark-results/local-20260404/bc-container.log`
- GitHub Actions run 23974655275 (same crash pattern, same offending test)
