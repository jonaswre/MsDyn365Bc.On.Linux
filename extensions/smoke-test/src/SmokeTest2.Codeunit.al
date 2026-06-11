// Smoke test codeunit 2: trivial string + boolean checks.
// Used by test-versions.yml to verify the container surface end-to-end
// across all supported BC versions.
codeunit 70001 "BC Container Smoke Test 2"
{
    Subtype = Test;
    TestPermissions = Disabled;

    [Test]
    procedure TestStringConcatenation()
    var
        SmokeLogic: Codeunit "BC Container Smoke Logic";
        Result: Text;
    begin
        Result := SmokeLogic.Greeting('World');
        if Result <> 'Hello, World' then
            Error('String concatenation failed: %1', Result);
    end;

    [Test]
    procedure TestBooleanLogic()
    var
        SmokeLogic: Codeunit "BC Container Smoke Logic";
    begin
        if not SmokeLogic.IsBooleanLogicSane() then
            Error('Boolean logic failed');
    end;
}
