// Smoke test codeunit 3: report rendering/export check for the parity workflow.
// This catches Linux/Windows differences in user-visible report output.
codeunit 70003 "BC Container Report Smoke Test"
{
    Subtype = Test;
    TestPermissions = Disabled;

    [Test]
    procedure TestCustomerListPdfExport()
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

        if not Report.SaveAs(Report::"Customer - List", '', ReportFormat::Pdf, OutStream, RecRef) then
            Error('Customer list PDF export failed');

        if not TempBlob.HasValue() then
            Error('Customer list PDF export returned no content');

        if TempBlob.Length() < 100 then
            Error('Customer list PDF export too small: %1 bytes', TempBlob.Length());
    end;
}
