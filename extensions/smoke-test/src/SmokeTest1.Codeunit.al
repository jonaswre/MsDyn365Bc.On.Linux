// Smoke test codeunit 1: trivial sanity checks (date + arithmetic).
// Used by test-versions.yml to verify the container surface end-to-end
// across all supported BC versions.
codeunit 70000 "BC Container Smoke Test 1"
{
    Subtype = Test;
    TestPermissions = Disabled;

    [Test]
    procedure TestDateSanity()
    var
        SmokeLogic: Codeunit "BC Container Smoke Logic";
    begin
        if not SmokeLogic.IsDateSane(Today()) then
            Error('Date sanity check failed: %1', Today());
    end;

    [Test]
    procedure TestBasicArithmetic()
    var
        SmokeLogic: Codeunit "BC Container Smoke Logic";
    begin
        if SmokeLogic.Multiply(6, 7) <> 42 then
            Error('Basic arithmetic failed');
    end;
}
