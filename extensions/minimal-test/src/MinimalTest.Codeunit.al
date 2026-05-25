codeunit 99800 "Minimal Math Test"
{
    Subtype = Test;

    [Test]
    procedure AdditionWorks()
    begin
        Assert(2 + 2 = 4, '2 + 2 must equal 4');
    end;

    [Test]
    procedure MultiplicationWorks()
    begin
        Assert(6 * 7 = 42, '6 * 7 must equal 42');
    end;

    local procedure Assert(Condition: Boolean; Msg: Text)
    begin
        if not Condition then
            Error(Msg);
    end;
}
