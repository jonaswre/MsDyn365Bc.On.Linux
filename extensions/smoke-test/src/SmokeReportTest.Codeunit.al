// Smoke test codeunit 3: report rendering/export check for the parity workflow.
// This catches Linux/Windows differences in user-visible report output.
codeunit 70003 "BC Container Report Smoke Test"
{
    Subtype = Test;
    TestPermissions = Disabled;

    [Test]
    procedure TestCustomerListPdfExport()
    begin
        AssertCustomerListExport(ReportFormat::Pdf, 'PDF');
    end;

    [Test]
    procedure TestCustomerListWordExport()
    begin
        AssertCustomerListExport(ReportFormat::Word, 'Word');
    end;

    [Test]
    procedure TestCustomerListExcelExport()
    begin
        AssertCustomerListExport(ReportFormat::Excel, 'Excel');
    end;

    local procedure AssertCustomerListExport(Format: ReportFormat; FormatLabel: Text)
    var
        Customer: Record Customer;
        TempBlob: Codeunit "Temp Blob";
        RecRef: RecordRef;
        OutStream: OutStream;
    begin
        if not Customer.FindFirst() then
            Error('No customer available for report export smoke test');

        Customer.SetRecFilter();
        RecRef.GetTable(Customer);
        TempBlob.CreateOutStream(OutStream);

        if not Report.SaveAs(Report::"Customer - List", '', Format, OutStream, RecRef) then
            Error('Customer list %1 export failed', FormatLabel);

        if not TempBlob.HasValue() then
            Error('Customer list %1 export returned no content', FormatLabel);

        if TempBlob.Length() < 100 then
            Error('Customer list %1 export too small: %2 bytes', FormatLabel, TempBlob.Length());
    end;
}
