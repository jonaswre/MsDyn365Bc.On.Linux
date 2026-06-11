// Normal application code exercised by smoke tests so coverage checks have
// a non-test object to instrument.
codeunit 70002 "BC Container Smoke Logic"
{
    procedure IsDateSane(Value: Date): Boolean
    begin
        exit(Value >= 20200101D);
    end;

    procedure Multiply(Left: Integer; Right: Integer): Integer
    begin
        exit(Left * Right);
    end;

    procedure Greeting(Name: Text): Text
    begin
        exit('Hello, ' + Name);
    end;

    procedure IsBooleanLogicSane(): Boolean
    begin
        exit(true and not false);
    end;
}
